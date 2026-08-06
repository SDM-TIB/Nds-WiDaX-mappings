from ckan.plugins import toolkit
import requests
import json
import logging
import logging.config
from datetime import date
import ckan.model as model
import ckan.logic as logic
from ckan.common import config
import ckan.plugins as plugins
from ckan.lib.search import index_for
from ckan.model import Session

from crontab import CronTab
import time

import sys
sys.setrecursionlimit(4000)  # Default is typically 1000

flask_d = toolkit.g
NotFound = logic.NotFound
NotAuthorized = logic.NotAuthorized


class LDM_DatasetImport:

    def __init__(self, ds_parser):
        # Dataset Parser
        self.ds_parser  = ds_parser

        # config updates
        self.ckan_virtual_env_path = '/usr/lib/ckan/default/bin/'
        self.root_path = '/usr/lib/ckan/default/src/ckanext-TIBimport/ckanext/tibimport/'
        self.crontab_user = config.get('tibimport.updatedatasets_crontab_user', "root")
        self.home_ur = config.get('ckan.site_url', "http://localhost:5000")
        self.update_enabled = toolkit.asbool(config.get('tibimport.updatedatasets_enabled', False))
        if ds_parser is None:
            default_log_path = '/usr/lib/ckan/default/src/ckanext-TIBimport/ckanext/tibimport/logs/'
            self.log_file_path = config.get('tibimport.log_file_path', default_log_path)
        else:
            self.log_file_path = ds_parser.log_file_path
        self.config_cronjobs()

        # Profiling configuration
        self.profiling_enabled = toolkit.asbool(config.get('tibimport.profiling_enabled', False))
        self.profiling_file = self.log_file_path + 'profiling_' + date.today().strftime("%Y_%m_%d") + '.log'
        
        # Indexing strategy configuration
        # Options:
        #   'default' - Use standard CKAN package_create/update with automatic indexing (SLOW for batch)
        #   'defer_commit' - Defer DB commits, but still triggers sync plugin at end (SLOW final commit)
        #   'defer_commit_manual_index' - Defer commits + manual indexing (COMPLEX)
        #   'defer_commit_rebuild_missing' - Defer commits + rebuild missing (WON'T WORK - context flags not accessible)
        #   'custom_rebuild_missing' - Disable plugin, commit per operation, rebuild at end (FAST, but see warning)
        #   'disable_plugin' - Disable/enable plugin per operation (SAFEST for concurrent use, slight overhead)
        #   'manual_batch' - Disable plugin once, manual indexing at end (SIMILAR to custom_rebuild_missing)
        #
        # RECOMMENDED for scheduled batch imports: 'custom_rebuild_missing'
        # RECOMMENDED for concurrent UI access: 'disable_plugin'
        #
        # WARNING for 'custom_rebuild_missing' and 'manual_batch':
        #   If users create/edit datasets via UI during batch import, those datasets won't be
        #   indexed until the batch finishes and rebuild runs. This creates a search delay.
        #   Solution: Run batch imports during maintenance windows or use 'disable_plugin' strategy.
        self.indexing_strategy = config.get('tibimport.indexing_strategy', 'default')
        
        # CKAN's API Actions
        self.context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}
        self.action_package_show = toolkit.get_action('package_show')
        self.action_organization_show = toolkit.get_action('organization_show')
        
        # Configure package create/update actions based on indexing strategy
        self._configure_indexing_actions()
        
        self.action_package_delete = toolkit.get_action('package_delete')
        self.action_organization_create = toolkit.get_action('organization_create')
        self.action_organization_delete = toolkit.get_action('organization_delete')
        self.action_organization_update = toolkit.get_action('organization_update')

        # Allow unauthorized ejecution
        toolkit.auth_allow_anonymous_access(self.action_package_show)
        toolkit.auth_allow_anonymous_access(self.action_organization_show)
        if self.indexing_strategy == 'default':
            toolkit.auth_allow_anonymous_access(self.action_package_create)
            toolkit.auth_allow_anonymous_access(self.action_package_update)
            toolkit.auth_allow_anonymous_access(self.action_package_delete)
        toolkit.auth_allow_anonymous_access(self.action_organization_create)
        toolkit.auth_allow_anonymous_access(self.action_organization_update)
        toolkit.auth_allow_anonymous_access(self.action_organization_delete)

        # Set to True to force organization's updates
        self.force_organization_update = False
        # Set to True to force organization update just once (Ex. Some profiles assign all datasets to the same Org)
        self.force_organization_update_only_once = False
        
        # Track package IDs for batch indexing
        self.batch_package_ids = []
        
        # Track if plugin was disabled for disable_plugin strategy
        self.plugin_was_disabled = False

    def _configure_indexing_actions(self):
        """Configure package create/update actions based on indexing strategy"""
        if self.indexing_strategy == 'default':
            self.action_package_create = toolkit.get_action('package_create')
            self.action_package_update = toolkit.get_action('package_update')
        elif self.indexing_strategy == 'defer_commit':
            self.action_package_create = self._package_create_defer_commit
            self.action_package_update = self._package_update_defer_commit
        elif self.indexing_strategy == 'defer_commit_manual_index':
            self.action_package_create = self._package_create_defer_commit_manual_index
            self.action_package_update = self._package_update_defer_commit_manual_index
        elif self.indexing_strategy == 'defer_commit_rebuild_missing':
            self.action_package_create = self._package_create_defer_commit_rebuild
            self.action_package_update = self._package_update_defer_commit_rebuild
        elif self.indexing_strategy == 'custom_rebuild_missing':
            self.action_package_create = self._package_create_custom_rebuild
            self.action_package_update = self._package_update_custom_rebuild
        elif self.indexing_strategy == 'disable_plugin':
            self.action_package_create = self._package_create_disable_plugin
            self.action_package_update = self._package_update_disable_plugin
        elif self.indexing_strategy == 'manual_batch':
            self.action_package_create = self._package_create_manual_batch
            self.action_package_update = self._package_update_manual_batch
        elif self.indexing_strategy == 'thread_local_flag':
            self.action_package_create = self._package_create_thread_flag
            self.action_package_update = self._package_update_thread_flag
        else:
            # Default fallback
            self.action_package_create = toolkit.get_action('package_create')
            self.action_package_update = toolkit.get_action('package_update')
            self.set_log_error(f"Unknown indexing strategy: {self.indexing_strategy}, using default")

    # INDEXING APPROACH 1: Default - Using standard CKAN methods with defer_commit
    def _package_create_defer_commit(self, context, data_dict):
        """
        Approach 1: Use defer_commit to delay database commits but still use automatic indexing.
        The synchronous search plugin will still trigger but commits are batched.
        """
        context = context.copy()
        context['defer_commit'] = True
        result = toolkit.get_action('package_create')(context, data_dict)
        return result
    
    def _package_update_defer_commit(self, context, data_dict):
        """
        Approach 1: Use defer_commit to delay database commits but still use automatic indexing.
        """
        context = context.copy()
        context['defer_commit'] = True
        result = toolkit.get_action('package_update')(context, data_dict)
        return result

    # INDEXING APPROACH 1.5: Hybrid - defer_commit with manual indexing
    def _package_create_defer_commit_manual_index(self, context, data_dict):
        """
        Hybrid Approach: Use defer_commit to batch database commits without automatic indexing.
        Disables plugin during operation to prevent automatic indexing, then manually indexes at the end.
        Combines the benefits of defer_commit (batched DB commits) with manual indexing control.
        """
        # Disable plugin to prevent automatic indexing
        # plugin_was_loaded = plugins.plugin_loaded('synchronous_search')
        # if plugin_was_loaded:
        #     plugins.unload('synchronous_search')
        
        try:
            context = context.copy()
            context['defer_commit'] = True
            result = toolkit.get_action('package_create')(context, data_dict)
            # Extract ID from result - it could be a string (ID) or dict (package)
            # pkg_id = result if isinstance(result, str) else result.get('id')
            pkg_id = result
            if pkg_id:
                self.batch_package_ids.append(pkg_id)
            return result
        finally:
            pass
        # finally:
        #     # Reload plugin after operation
        #     if plugin_was_loaded and not plugins.plugin_loaded('synchronous_search'):
        #         plugins.load('synchronous_search')
    
    def _package_update_defer_commit_manual_index(self, context, data_dict):
        """
        Hybrid Approach: Use defer_commit to batch database commits without automatic indexing.
        Disables plugin during operation to prevent automatic indexing, then manually indexes at the end.
        """
        # Disable plugin to prevent automatic indexing
        plugin_was_loaded = plugins.plugin_loaded('synchronous_search')
        if plugin_was_loaded:
            plugins.unload('synchronous_search')
        
        try:
            context = context.copy()
            context['defer_commit'] = True
            result = toolkit.get_action('package_update')(context, data_dict)
            # Extract ID from result - it could be a string (ID) or dict (package)
            pkg_id = result if isinstance(result, str) else result.get('id')
            if pkg_id and pkg_id not in self.batch_package_ids:
                self.batch_package_ids.append(pkg_id)
            return result
        finally:
            # Reload plugin after operation
            if plugin_was_loaded and not plugins.plugin_loaded('synchronous_search'):
                plugins.load('synchronous_search')

    # INDEXING APPROACH 1.6: defer_commit with CKAN rebuild -o (only_missing) - Custom implementation
    def _package_create_defer_commit_rebuild(self, context, data_dict):
        """
        Custom package creation that prevents synchronization during batch import.
        Uses context flags to signal batch mode to plugins.
        """
        # Add context flags to prevent search indexing
        context = context.copy()
        context['defer_commit'] = True
        context['skip_search_indexing'] = True  # Signal to plugins to skip indexing
        context['batch_mode'] = True  # Additional flag for batch processing
        
        # Use standard package_create with special context
        result = toolkit.get_action('package_create')(context, data_dict)
        return result
    
    
    def _package_update_defer_commit_rebuild(self, context, data_dict):
        """
        Custom package update that prevents synchronization during batch import.
        Uses context flags to signal batch mode to plugins.
        """
        # Add context flags to prevent search indexing
        context = context.copy()
        context['defer_commit'] = True
        context['skip_search_indexing'] = True  # Signal to plugins to skip indexing
        context['batch_mode'] = True  # Additional flag for batch processing
        
        # Use standard package_update with special context
        result = toolkit.get_action('package_update')(context, data_dict)
        return result
    

    # INDEXING APPROACH 1.7: Custom package methods with rebuild -o (without defer_commit)
    def _package_create_custom_rebuild(self, context, data_dict):
        """
        Custom package creation using CKAN models that bypasses plugin hooks.
        This variant does NOT use defer_commit - commits happen per operation.
        """
        # Disable plugin on first call
        if not self.plugin_was_disabled and plugins.plugin_loaded('synchronous_search'):
            plugins.unload('synchronous_search')
            self.plugin_was_disabled = True
            self.set_log_info("Disabled synchronous_search for custom_rebuild_missing strategy")
        
        # Use our custom package create without defer_commit
        context = context.copy()
        context['defer_commit'] = False  # Commit immediately
        return self._custom_package_create(context, data_dict)
    
    def _package_update_custom_rebuild(self, context, data_dict):
        """
        Custom package update using CKAN models that bypasses plugin hooks.
        This variant does NOT use defer_commit - commits happen per operation.
        """
        # Disable plugin on first call
        if not self.plugin_was_disabled and plugins.plugin_loaded('synchronous_search'):
            plugins.unload('synchronous_search')
            self.plugin_was_disabled = True
            self.set_log_info("Disabled synchronous_search for custom_rebuild_missing strategy")
        
        # Use our custom package update without defer_commit
        context = context.copy()
        context['defer_commit'] = False  # Commit immediately
        return self._custom_package_update(context, data_dict)

    # INDEXING APPROACH 2: Temporarily disable synchronous_search plugin per operation
    def _package_create_disable_plugin(self, context, data_dict):
        """
        Approach 2: Temporarily disable the synchronous_search plugin for each operation.
        The plugin is unloaded before each operation and reloaded immediately after.
        Indexing will be done manually in batch at the end.
        """
        plugin_was_loaded = plugins.plugin_loaded('synchronous_search')
        
        if plugin_was_loaded:
            plugins.unload('synchronous_search')
        
        try:
            result = toolkit.get_action('package_create')(context, data_dict)
            self.batch_package_ids.append(result)
            return result
        finally:
            # Reload plugin immediately after operation if it was loaded
            if plugin_was_loaded and not plugins.plugin_loaded('synchronous_search'):
                plugins.load('synchronous_search')
    
    def _package_update_disable_plugin(self, context, data_dict):
        """
        Approach 2: Temporarily disable the synchronous_search plugin for each operation.
        The plugin is unloaded before each operation and reloaded immediately after.
        """
        plugin_was_loaded = plugins.plugin_loaded('synchronous_search')
        
        if plugin_was_loaded:
            plugins.unload('synchronous_search')
        
        try:
            result = toolkit.get_action('package_update')(context, data_dict)
            if result not in self.batch_package_ids:
                self.batch_package_ids.append(result)
            return result
        finally:
            # Reload plugin immediately after operation if it was loaded
            if plugin_was_loaded and not plugins.plugin_loaded('synchronous_search'):
                plugins.load('synchronous_search')

    # INDEXING APPROACH 3.5: Thread-local flag to skip indexing (BEST SOLUTION)
    def _package_create_thread_flag(self, context, data_dict):
        """
        Approach 3.5: Use thread-local flag in ldm_synchronous_search plugin.
        This is the best solution as it:
        - Has NO plugin load/unload overhead
        - Is thread-safe (each thread has its own flag)
        - Allows concurrent user operations to be indexed normally
        - Clean and simple implementation
        
        Requires: ldm_synchronous_search plugin to be loaded instead of default synchronous_search
        """
        from ckanext.ldm_synchronous_search.plugin import set_batch_mode
        
        # Enable batch mode on first call
        if not getattr(self, '_batch_mode_set', False):
            set_batch_mode(True)
            self._batch_mode_set = True
            self.set_log_info("Enabled batch mode for current thread")
        
        return toolkit.get_action('package_create')(context, data_dict)
    
    def _package_update_thread_flag(self, context, data_dict):
        """
        Approach 3.5: Use thread-local flag in ldm_synchronous_search plugin.
        """
        from ckanext.ldm_synchronous_search.plugin import set_batch_mode
        
        # Enable batch mode on first call
        if not getattr(self, '_batch_mode_set', False):
            set_batch_mode(True)
            self._batch_mode_set = True
            self.set_log_info("Enabled batch mode for current thread")
        
        return toolkit.get_action('package_update')(context, data_dict)

    # INDEXING APPROACH 3: Manual batch indexing with complete control
    def _package_create_manual_batch(self, context, data_dict):
        """
        Approach 3: Pure manual control - plugin should be disabled externally.
        Does NOT manage plugin state - assumes plugin is already disabled.
        Most efficient for large batches as there's no plugin management overhead.
        """
        result = toolkit.get_action('package_create')(context, data_dict)
        self.batch_package_ids.append(result)
        return result
    
    def _package_update_manual_batch(self, context, data_dict):
        """
        Approach 3: Pure manual control - plugin should be disabled externally.
        Does NOT manage plugin state - assumes plugin is already disabled.
        """
        result = toolkit.get_action('package_update')(context, data_dict)
        if result not in self.batch_package_ids:
            self.batch_package_ids.append(result)
        return result

    def finalize_batch_indexing(self):
        """
        Finalize batch operations based on the indexing strategy.
        This should be called after all package operations are complete.
        """
        start_time = time.time()
        
        if self.indexing_strategy == 'defer_commit':
            # Commit all deferred database changes
            model.repo.commit()
            index_time = time.time() - start_time
            self.set_log_info(f"Finalized defer_commit strategy. Commit time: {index_time:.2f}s")
            if self.profiling_enabled:
                self._write_profiling(f"Batch commit time: {index_time:.2f}s\n")
        
        elif self.indexing_strategy == 'defer_commit_manual_index':
            # First, commit all deferred database changes BEFORE disabling plugin
            # This ensures packages exist in DB before we try to read them for indexing

            # Now disable the plugin to prevent automatic indexing
            plugin_was_loaded = plugins.plugin_loaded('synchronous_search')
            if plugin_was_loaded:
                plugins.unload('synchronous_search')
                self.set_log_info("Disabled synchronous_search before indexing for defer_commit_manual_index")

            model.repo.commit()
            commit_time = time.time() - start_time
            self.set_log_info(f"Committed {len(self.batch_package_ids)} deferred database changes")
            

            
            # Now manually index all packages (they exist in DB now)
            index_start = time.time()
            package_index = index_for(model.Package)
            context = {'model': model, 'ignore_auth': True, 'validate': False}
            
            indexed_count = 0
            for pkg_id in self.batch_package_ids:
                try:
                    self.set_log_info(f"Attempting to index package: {pkg_id.get('name')} (type: {type(pkg_id).__name__})")
                    # pkg_dict = toolkit.get_action('package_show')(context, {'id': pkg_id.get('name')})
                    pkg_dict = pkg_id
                    package_index.update_dict(pkg_dict, defer_commit=True)
                    indexed_count += 1
                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    self.set_log_error(f"Error indexing package {pkg_id.get('name')}: {type(e).__name__}: {str(e)}\n{error_detail}")
            
            # Single commit to SOLR
            package_index.commit()
            
            # Reload plugin if it was loaded
            if plugin_was_loaded:
                plugins.load('synchronous_search')
                self.set_log_info("Re-enabled synchronous_search after indexing")
            
            # Clear the batch list
            self.batch_package_ids = []
            
            index_time = time.time() - index_start
            total_time = time.time() - start_time
            self.set_log_info(f"defer_commit_manual_index: Commit {commit_time:.2f}s, Indexed {indexed_count} packages in {index_time:.2f}s, Total {total_time:.2f}s")
            if self.profiling_enabled:
                self._write_profiling(f"Batch commit time: {commit_time:.2f}s\n")
                self._write_profiling(f"Batch indexing: {indexed_count} packages in {index_time:.2f}s\n")
                self._write_profiling(f"Total finalization time: {total_time:.2f}s\n")
        
        elif self.indexing_strategy == 'defer_commit_rebuild_missing':
            # Commit all deferred database changes
            model.repo.commit()
            commit_time = time.time() - start_time
            self.set_log_info(f"Committed all deferred database changes for defer_commit_rebuild_missing in {commit_time:.2f}s")
            
            # Now use CKAN's rebuild() function with only_missing=True
            # This is equivalent to: ckan search-index rebuild -o
            index_start = time.time()
            from ckan.lib.search import rebuild, commit
            
            try:
                self.set_log_info("Starting rebuild with only_missing for defer_commit_rebuild_missing strategy")
                
                # rebuild with only_missing=True, defer_commit=True (no commit per package)
                rebuild(package_id=None,
                       only_missing=True,
                       force=False,
                       refresh=False,
                       defer_commit=True,  # Don't commit after each package
                       quiet=False)
                
                # Single commit to SOLR at the end
                commit()
                
                index_time = time.time() - index_start
                total_time = time.time() - start_time
                self.set_log_info(f"defer_commit_rebuild_missing: Commit {commit_time:.2f}s, Rebuild index {index_time:.2f}s, Total {total_time:.2f}s")
                
                if self.profiling_enabled:
                    self._write_profiling(f"Batch commit time: {commit_time:.2f}s\n")
                    self._write_profiling(f"Rebuild index (only_missing): {index_time:.2f}s\n")
                    self._write_profiling(f"Total finalization time: {total_time:.2f}s\n")
            except Exception as e:
                self.set_log_error(f"Error during rebuild index: {str(e)}")
                import traceback
                self.set_log_error(traceback.format_exc())
        
        elif self.indexing_strategy == 'custom_rebuild_missing':
            # No deferred commits to finalize (commits happened per operation)
            # Just use CKAN's rebuild() function with only_missing=True
            index_start = time.time()
            from ckan.lib.search import rebuild, commit
            
            try:
                self.set_log_info("Starting rebuild with only_missing for custom_rebuild_missing strategy")
                
                # rebuild with only_missing=True, defer_commit=True (no commit per package)
                rebuild(package_id=None,
                       only_missing=True,
                       force=False,
                       refresh=False,
                       defer_commit=True,  # Don't commit after each package
                       quiet=False)
                
                # Single commit to SOLR at the end
                commit()
                
                index_time = time.time() - index_start
                self.set_log_info(f"custom_rebuild_missing: Rebuild index completed in {index_time:.2f}s")
                
                if self.profiling_enabled:
                    self._write_profiling(f"Rebuild index (only_missing): {index_time:.2f}s\n")
            except Exception as e:
                self.set_log_error(f"Error during rebuild index: {str(e)}")
                import traceback
                self.set_log_error(traceback.format_exc())
            finally:
                # Reload plugin if it was disabled
                if self.plugin_was_disabled and not plugins.plugin_loaded('synchronous_search'):
                    plugins.load('synchronous_search')
                    self.plugin_was_disabled = False
                    self.set_log_info("Re-enabled synchronous_search after rebuild")
        
        elif self.indexing_strategy in ['disable_plugin', 'manual_batch']:
            # Reload the synchronous_search plugin if it's not loaded
            if not plugins.plugin_loaded('synchronous_search'):
                plugins.load('synchronous_search')
            
            # Manual batch indexing
            package_index = index_for(model.Package)
            context = {'model': model, 'ignore_auth': True, 'validate': False}
            
            indexed_count = 0
            for pkg_id in self.batch_package_ids:
                try:
                    pkg_dict = toolkit.get_action('package_show')(context, {'id': pkg_id})
                    package_index.update_dict(pkg_dict, defer_commit=True)
                    indexed_count += 1
                except Exception as e:
                    self.set_log_error(f"Error indexing package {pkg_id}: {str(e)}")
            
            # Single commit to SOLR
            package_index.commit()
            
            # Clear the batch list
            self.batch_package_ids = []
            
            index_time = time.time() - start_time
            self.set_log_info(f"Batch indexed {indexed_count} packages in {index_time:.2f}s")
            if self.profiling_enabled:
                self._write_profiling(f"Batch indexing: {indexed_count} packages in {index_time:.2f}s\n")
        
        elif self.indexing_strategy == 'thread_local_flag':
            # Disable batch mode and rebuild missing packages
            from ckanext.ldm_synchronous_search.plugin import set_batch_mode
            from ckan.lib.search import rebuild, commit
            
            # Disable batch mode before rebuild
            set_batch_mode(False)
            self._batch_mode_set = False
            self.set_log_info("Disabled batch mode for current thread")
            
            # Rebuild only missing packages
            index_start = time.time()
            try:
                self.set_log_info("Starting rebuild with only_missing for thread_local_flag strategy")
                
                rebuild(package_id=None,
                       only_missing=True,
                       force=False,
                       refresh=False,
                       defer_commit=True,
                       quiet=False)
                
                commit()
                
                index_time = time.time() - index_start
                total_time = time.time() - start_time
                self.set_log_info(f"thread_local_flag: Rebuild completed in {index_time:.2f}s, Total {total_time:.2f}s")
                
                if self.profiling_enabled:
                    self._write_profiling(f"Rebuild index (only_missing): {index_time:.2f}s\n")
                    self._write_profiling(f"Total finalization time: {total_time:.2f}s\n")
            except Exception as e:
                self.set_log_error(f"Error during rebuild index: {str(e)}")
                import traceback
                self.set_log_error(traceback.format_exc())
        
        elif self.indexing_strategy == 'default':
            # Nothing special to do for default strategy
            self.set_log_info("Using default indexing strategy - no finalization needed")

    def _write_profiling(self, message):
        """Write profiling information to the profiling file"""
        try:
            with open(self.profiling_file, 'a') as f:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"[{timestamp}] {message}")
        except Exception as e:
            self.set_log_error(f"Error writing profiling data: {str(e)}")

    # AUTOUPDATE IMPORTED DATASETS
    # ****************************

    def config_cronjobs(self):
        # ┌───────────── minute(0 - 59)
        # │ ┌───────────── hour(0 - 23)
        # │ │ ┌───────────── day of month(1 - 31)
        # │ │ │ ┌───────────── month(1 - 12)
        # │ │ │ │ ┌───────────── day of week(0 - 6)(Sunday to Saturday;
        # │ │ │ │ │                                       7 is also Sunday on some systems)
        # │ │ │ │ │
        # │ │ │ │ │
        # * * * * *command to execute
        # * any value
        # , value list separator
        # -    range of values
        # / step values Ex: */10 each 10
        # job.setall('2 10 * * *')  10:02 every day
        # list in console: crontab -l

        self.background_jobs = {
                   'luh':
                       {'title': 'update_datasets_luh',
                        'method': 'TIB_update_imported_datasets_luh',
                        'comment': "TIB_update_imported_datasets_luh",
                        'crontab_commands': [".setall('0 0 2 * *')"]},
                   'radar':
                       {'title': 'update_datasets_radar',
                        'method': 'TIB_update_imported_datasets_radar',
                        'comment': "TIB_update_imported_datasets_radar",
                        'crontab_commands': [".setall('0 0 3 * *')"]},
                   'pangea_agriculture':
                       {'title': 'update_datasets_pangea_agriculture',
                        'method': 'TIB_update_imported_datasets_pangea_agriculture',
                        'comment': "TIB_update_imported_datasets_pangea_agriculture",
                        'crontab_commands': [".setall('0 0 4 * *')"]},
                    'pangea_chemistry':
                        {'title': 'update_datasets_pangea_chemistry',
                         'method': 'TIB_update_imported_datasets_pangea_chemistry',
                         'comment': "TIB_update_imported_datasets_pangea_chemistry",
                         'crontab_commands': [".setall('0 1 4 * *')"]},
                    'pangea_lithosphere':
                        {'title': 'update_datasets_pangea_lithosphere',
                         'method': 'TIB_update_imported_datasets_pangea_lithosphere',
                         'comment': "TIB_update_imported_datasets_pangea_lithosphere",
                         'crontab_commands': [".setall('0 2 4 * *')"]},
                    'pangea_atmosphere':
                        {'title': 'update_datasets_pangea_atmosphere',
                         'method': 'TIB_update_imported_datasets_pangea_atmosphere',
                         'comment': "TIB_update_imported_datasets_pangea_atmosphere",
                         'crontab_commands': [".setall('0 3 4 * *')"]},
                    'pangea_biologicalclassification':
                        {'title': 'update_datasets_pangea_biologicalclassification',
                         'method': 'TIB_update_imported_datasets_pangea_biologicalclassification',
                         'comment': "TIB_update_imported_datasets_pangea_biologicalclassification",
                         'crontab_commands': [".setall('0 4 4 * *')"]},
                    'pangea_paleontology':
                        {'title': 'update_datasets_pangea_paleontology',
                         'method': 'TIB_update_imported_datasets_pangea_paleontology',
                         'comment': "TIB_update_imported_datasets_pangea_paleontology",
                         'crontab_commands': [".setall('0 5 4 * *')"]},
                    'pangea_oceans':
                        {'title': 'update_datasets_pangea_oceans',
                         'method': 'TIB_update_imported_datasets_pangea_oceans',
                         'comment': "TIB_update_imported_datasets_pangea_oceans",
                         'crontab_commands': [".setall('0 0 5 * *')"]},
                    'pangea_ecology':
                        {'title': 'update_datasets_pangea_ecology',
                         'method': 'TIB_update_imported_datasets_pangea_ecology',
                         'comment': "TIB_update_imported_datasets_pangea_ecology",
                         'crontab_commands': [".setall('0 1 5 * *')"]},
                    'pangea_landsurface':
                        {'title': 'update_datasets_pangea_landsurface',
                         'method': 'TIB_update_imported_datasets_pangea_landsurface',
                         'comment': "TIB_update_imported_datasets_pangea_landsurface",
                         'crontab_commands': [".setall('0 2 5 * *')"]},
                    'pangea_biosphere':
                        {'title': 'update_datasets_pangea_biosphere',
                         'method': 'TIB_update_imported_datasets_pangea_biosphere',
                         'comment': "TIB_update_imported_datasets_pangea_biosphere",
                         'crontab_commands': [".setall('0 3 5 * *')"]},
                    'pangea_geophysics':
                        {'title': 'update_datasets_pangea_geophysics',
                         'method': 'TIB_update_imported_datasets_pangea_geophysics',
                         'comment': "TIB_update_imported_datasets_pangea_geophysics",
                         'crontab_commands': [".setall('0 4 5 * *')"]},
                    'pangea_cryosphere':
                        {'title': 'update_datasets_pangea_cryosphere',
                         'method': 'TIB_update_imported_datasets_pangea_cryosphere',
                         'comment': "TIB_update_imported_datasets_pangea_cryosphere",
                         'crontab_commands': [".setall('0 5 5 * *')"]},
                    'pangea_lakesandrivers':
                        {'title': 'update_datasets_pangea_lakesandrivers',
                         'method': 'TIB_update_imported_datasets_pangea_lakesandrivers',
                         'comment': "TIB_update_imported_datasets_pangea_lakesandrivers",
                         'crontab_commands': [".setall('0 0 6 * *')"]},
                    'pangea_humandimensions':
                        {'title': 'update_datasets_pangea_humandimensions',
                         'method': 'TIB_update_imported_datasets_pangea_humandimensions',
                         'comment': "TIB_update_imported_datasets_pangea_humandimensions",
                         'crontab_commands': [".setall('0 1 6 * *')"]},
                    'pangea_fisheries':
                        {'title': 'update_datasets_pangea_fisheries',
                         'method': 'TIB_update_imported_datasets_pangea_fisheries',
                         'comment': "TIB_update_imported_datasets_pangea_fisheries",
                         'crontab_commands': [".setall('0 2 6 * *')"]},
                    'leopard':
                        {'title': 'update_datasets_leopard',
                         'method': 'TIB_update_imported_datasets_leopard',
                         'comment': "TIB_update_imported_datasets_leopard",
                         'crontab_commands': [".setall('0 3 6 * *')"]}, 
                    'osnadata':
                        {'title': 'update_datasets_osnadata',
                         'method': 'TIB_update_imported_datasets_osnadata',
                         'comment': "TIB_update_imported_datasets_osnadata",
                         'crontab_commands': [".setall('0 4 6 * *')"]}, 
                    'goettingen':
                        {'title': 'update_datasets_goettingen',
                         'method': 'TIB_update_imported_datasets_goettingen',
                         'comment': "TIB_update_imported_datasets_goettingen",
                         'crontab_commands': [".setall('0 5 6 * *')"]},
                    'leuphana':
                        {'title': 'update_datasets_leuphana',
                         'method': 'TIB_update_imported_datasets_leuphana',
                         'comment': "TIB_update_imported_datasets_leuphana",
                         'crontab_commands': [".setall('0 0 7 * *')"]},                    

        }

    def get_background_jobs(self):
        return self.background_jobs

    def create_cronjobs(self):

        cron = CronTab(user=self.crontab_user)
        for job in cron.find_comment('tib_update_imported_datasets'):
            cron.remove(job)

        if self.update_enabled:
            command_base = self.ckan_virtual_env_path+'python3 ' + self.root_path + 'run_importation_update.py -t '
            # Define cronjobs
            for key,cronjob in self.background_jobs.items():
                command = command_base + key + " >> " + self.log_file_path + "crontab_log.txt 2>&1"
                job = cron.new(command=command, comment="tib_update_imported_datasets")
                job.env['home_path'] = self.home_ur
                for c in cronjob['crontab_commands']:
                    eval('job'+c)


        cron.write()

    # LOCAL CKAN INTERACTION
    # **********************

    def get_local_dataset(self, name):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.get.package_show
        # Note: Returns data even with dataset deleted => ds['state'] = 'deleted'
        params = {'id': name}
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}
        #context['return_id_only'] = False
        #context = {}

        try:
            #self.set_log_info("XXXXXXXX " + str(context) + " ZZZZZZZ" + str(params))
            #toolkit.auth_allow_anonymous_access(self.action_package_show)
            result = self.action_package_show(context, params)
        except NotFound as e:
            return {}
        return result

    def get_local_organization(self, name):

        # Just retrieve basic organixation metadata
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.get.organization_show
        params = {'id': name,
                   'include_datasets': 'false',
                   'include_dataset_count': 'false',
                   'include_users': 'false',
                   'include_groups': 'false',
                   'include_followers': 'false',
                   'include_extras': 'false'}
        context = {}

        try:
            result = self.action_organization_show(context, params)
        except NotFound as e:
            return {}
        except NotAuthorized as e:
            return {}
        return result

    def insert_dataset(self, ds_dict):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.create.package_create
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}

        # context['return_id_only'] = True
        self.set_log_info("XXXXXXXX " + str(context) + " ZZZZZZZ" + str(ds_dict))
        result = self.action_package_create(context, ds_dict)
        return result

    def update_dataset(self, ds_dict):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.update.package_update
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}

        # context['return_id_only'] = True
        result = self.action_package_update(context, ds_dict)
        return result

    def delete_dataset(self, name):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.delete.package_delete
        ds_dict = {"id": name}
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}

        self.action_package_delete(context, ds_dict)

    def insert_organization(self, org_dict):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.create.organization_create
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}
        self.action_organization_create(context, org_dict)

    def update_organization(self, ds_dict):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.update.organization_update
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}

        context['return_id_only'] = True
        self.action_organization_update(context, ds_dict)

    def delete_organization(self, name):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.delete.organization_delete
        org_dict = {"id": name}
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}
        self.action_organization_delete(context, org_dict)

    # REMOTE REPOSITORY INTERACTION
    # **********************

    def get_remote_datasets(self):
        '''
            Using the Dataset Parser (DatasetParser) retrieves all remote datasets adjusted
            to the dictionary requirements of CKAN and LDM (virtual Datasets schema).
        '''
        datasets_dict = self.ds_parser.get_all_datasets_dicts()
        return datasets_dict

    def get_remote_datasets_paged(self, page_url=''):
        '''
         Using the Dataset Parser (DatasetParser) retrieves all remote datasets adjusted
         to the dictionary requirements of CKAN and LDM (virtual Datasets schema).
         Instead of process all available Datasets, this method is processing in blocks or pages.
        '''
        datasets_dict = self.ds_parser.get_remote_datasets_paged(page_url)
        return datasets_dict

    def get_remote_organization(self, name):
        '''
            Using the Dataset Parser (DatasetParser) retrieves a remote organization
            to the dictionary requirements of CKAN and LDM.
        '''
        org_dict = self.ds_parser.get_organization(name)
        return org_dict


    # IMPORTATION METHODS
    # ********************

    def import_datasets(self):
        batch_start_time = time.time()
        
        # For manual_batch strategy, disable plugin once at the beginning
        if self.indexing_strategy == 'manual_batch':
            if plugins.plugin_loaded('synchronous_search'):
                plugins.unload('synchronous_search')
                self.plugin_was_disabled = True
                self.set_log_info("Disabled synchronous_search plugin for manual_batch strategy")
        
        remote_datasets = self.get_remote_datasets()

        # All datasets
        for ds in remote_datasets:
            self._insert_update_skip_dataset(ds)

        # For testing just 10
        # for x in range(10):
        #    self._insert_update_skip_dataset(remote_datasets[x])
        # self._insert_update_skip_dataset(remote_datasets[3])
        
        # Finalize batch indexing if needed
        self.finalize_batch_indexing()
        
        batch_total_time = time.time() - batch_start_time
        
        if self.profiling_enabled:
            self._write_profiling(f"Total import time: {batch_total_time:.2f}s for {len(remote_datasets)} datasets\n")
            self._write_profiling(f"Average time per dataset: {batch_total_time/len(remote_datasets):.2f}s\n")
            self._write_profiling(f"Indexing strategy used: {self.indexing_strategy}\n\n")
        
        self.set_log_info(self.ds_parser.get_summary_log())

    def import_datasets_paged(self, resumption_token=''):
        batch_start_time = time.time()
        total_datasets = 0
        
        # For manual_batch strategy, disable plugin once at the beginning
        if self.indexing_strategy == 'manual_batch':
            if plugins.plugin_loaded('synchronous_search'):
                plugins.unload('synchronous_search')
                self.plugin_was_disabled = True
                self.set_log_info("Disabled synchronous_search plugin for manual_batch strategy")
        
        remote_datasets_res = self.get_remote_datasets_paged(resumption_token)
        remote_datasets = remote_datasets_res["ds_list"]
        resumption_token = remote_datasets_res["resumptionToken"]
        
        # import first page
        for ds in remote_datasets:
            self._insert_update_skip_dataset(ds)
        total_datasets += len(remote_datasets)
        
        if self.indexing_strategy == 'defer_commit_manual_index':
            self.finalize_batch_indexing()

        # Import All the rest of pages
        while resumption_token != "":
            remote_datasets_res = self.get_remote_datasets_paged(resumption_token)
            remote_datasets = remote_datasets_res["ds_list"]
            resumption_token = remote_datasets_res["resumptionToken"]  
            for ds in remote_datasets:
                self._insert_update_skip_dataset(ds)
            total_datasets += len(remote_datasets)
            if self.indexing_strategy == 'defer_commit_manual_index':
                self.finalize_batch_indexing()
        
        # Finalize batch indexing if needed
        self.finalize_batch_indexing()
        
        batch_total_time = time.time() - batch_start_time
        
        if self.profiling_enabled:
            self._write_profiling(f"Total paged import time: {batch_total_time:.2f}s for {total_datasets} datasets\n")
            self._write_profiling(f"Average time per dataset: {batch_total_time/total_datasets:.2f}s\n")
            self._write_profiling(f"Indexing strategy used: {self.indexing_strategy}\n\n")
        
        self.set_log_info(self.ds_parser.get_summary_log())

        
    def _insert_update_skip_dataset(self, remote_dataset):
        # Start profiling for this dataset
        dataset_start_time = time.time()
        processing_start_time = time.time()
        
        ds_name = remote_dataset['name']
        org_name = remote_dataset['organization']['name']

        # Search the dataset locally
        self.set_log_info("Processing Dataset: " + ds_name + "...")
        dataset = self.get_local_dataset(ds_name)

        operation = None
        insertion_start_time = None
        
        if dataset == {}: # Not Found
            # Insert Organization first
            self._insert_skip_organization(remote_dataset['organization'])
            # discard remote id
            remote_dataset['id'] = ''
            # Insert Dataset
            remote_dataset = self.ds_parser.execute_before_insert_dataset(remote_dataset)
            
            processing_time = time.time() - processing_start_time
            insertion_start_time = time.time()
            
            self.insert_dataset(remote_dataset)
            
            insertion_time = time.time() - insertion_start_time
            operation = 'INSERT'
            
            self.ds_parser.increment_inserted_log()
            self.set_log_info("Dataset: " + ds_name + " Inserted")

        elif not self.ds_parser.should_be_updated(dataset, remote_dataset):
            # Skip Dataset - No changes
            processing_time = time.time() - processing_start_time
            insertion_time = 0
            operation = 'SKIP'
            
            self.ds_parser.increment_skiped_log()
            self.set_log_info("Dataset: "+ ds_name +" Skiped - No changes")

        else:
            # Update Dataset
            # use local id
            # Organization could change and need to be inserted first
            self._insert_skip_organization(remote_dataset['organization'])

            remote_dataset['id'] = dataset['id']
            remote_dataset = self.ds_parser.execute_before_update_dataset(remote_dataset)
            
            processing_time = time.time() - processing_start_time
            insertion_start_time = time.time()
            
            self.update_dataset(remote_dataset)
            
            insertion_time = time.time() - insertion_start_time
            operation = 'UPDATE'
            
            self.ds_parser.increment_modified_log()
            self.set_log_info("Dataset: " + ds_name + " Updated" + str(remote_dataset))
        
        # Calculate total time
        total_time = time.time() - dataset_start_time
        
        # Write profiling data if enabled
        if self.profiling_enabled:
            self._write_profiling(
                f"Dataset: {ds_name} | Operation: {operation} | "
                f"Processing: {processing_time:.3f}s | Insertion: {insertion_time:.3f}s | "
                f"Total: {total_time:.3f}s\n"
            )


    def _insert_skip_organization(self, org_dict):
        # Search the organization locally
        org = self.get_local_organization(org_dict['name'])
        if org == {}: # Not Found
            # Insert organization
            org_insert_dict = self.ds_parser.get_organization(org_dict['name'])
            self.insert_organization(org_insert_dict)
            self.set_log_info("Organization: " + org_insert_dict['name'] + " Inserted")
        elif self.force_organization_update:
            # Update Organization
            org_insert_dict = self.ds_parser.get_organization(org_dict['name'])
            org_insert_dict['id'] = org_dict['name']
            self.update_organization(org_insert_dict)
            self.set_log_info("Organization: " + org_insert_dict['name'] + " Updated")
            if self.force_organization_update_only_once:
                self.force_organization_update = False
        else:
            self.set_log_info("Organization: " + org_dict['name'] + " Skiped - Already exists")


    # LOGGER METHODS
    # **************

    def set_log_info(self, msg):
        self.ds_parser.logger.message = msg
        self.ds_parser.set_log_msg_info()

    def set_log_error(self, msg):
        self.ds_parser.logger.message = msg
        self.ds_parser.set_log_msg_error()

    def get_summary_log(self):
        return self.ds_parser.get_summary_log()





class DatasetParser():
    '''

    A Class used as reference defining the behavior that subclasses should implement
    for each particular source repository.

    '''

    def __init__(self):
        '''
        Profiles inherited from this class should define the following values
        self.log_file_prefix = "LUH_" # example
        '''
        self._config_logger()


    def get_all_datasets_dicts(self):
        '''
        This method should be implemented inside a Dataset Parser Profile
        Returning an array of dicts with all remote datasets dictionaries
        '''
        pass

    def get_organization(self, name):
        '''
            This method should be implemented inside a Dataset Parser Profile
            Returning a dictionary with an organization metadata
        '''
        pass

    def adjust_dataset_name(self, ds_name):
        '''
        This method should be implemented inside a Dataset Parser Profile
        Returning the dataset name (id) just as will be recorded in CKAN
        '''
        pass

    def should_be_updated(self, local_dataset, remote_dataset):
        '''
            This method should be implemented inside a Dataset Parser Profile
            Returning True or False after datasets comparison
        '''
        pass

    def execute_before_insert_dataset(self, remote_dataset):
        '''
            This method should be implemented inside a Dataset Parser Profile
            if needed. Allows to run specific modifications over the dataset to be inserted
        '''
        return remote_dataset

    def execute_before_update_dataset(self, remote_dataset):
        '''
            This method should be implemented inside a Dataset Parser Profile
            if needed. Allows to run specific modifications over the dataset to be inserted
        '''
        return remote_dataset

    def check_current_schema(self):
        '''
            This method should be implemented inside a Dataset Parser Profile
            Returning a dict with the results of comparing the metadata schema used in the code
            with the metadata schema retrieved by remote servers

            result = {'status_ok': True,
                      'report': 'Text explaining the results'}
                '''
        pass

    # LOGGER METHODS
    # **************

    def _config_logger(self):
        '''
            The order of logging levels is:
            DEBUG < INFO < WARNING < ERROR < CRITICAL
        '''
        logger = logging.getLogger('tibimport_parseprofile')
        logger.setLevel(logging.DEBUG)
        default_log_path = '/usr/lib/ckan/default/src/ckanext-TIBimport/ckanext/tibimport/logs/'
        self.log_file_path = config.get('tibimport.log_file_path', default_log_path)
        self.log_file = self.log_file_prefix+date.today().strftime("%Y_%m_%d")+"_log.log"
        fh = logging.FileHandler(self.log_file_path+self.log_file)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        fh.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(fh)
        self.logger = logger
        self.logger.message = ""
        self.reset_summary_logger()

    def set_log_msg_info(self):
        self.logger.info(self.logger.message)

    def set_log_msg_error(self):
        self.logger.error(self.logger.message)

    def reset_summary_logger(self):
        self.logger.datasets_inserted = 0
        self.logger.datasets_modified = 0
        self.logger.datasets_skiped = 0

    def get_summary_log(self):
        summary_log = {'Repository_name': self.repository_name,
                       "Datasets_inserted": self.logger.datasets_inserted,
                       "Datasets_updated": self.logger.datasets_modified,
                       "Datasets_skiped": self.logger.datasets_skiped,
                       "LOG_file": self.log_file_path+self.log_file,
                       "SCHEMA_REPORT": self.check_current_schema()}

        return summary_log

    def increment_inserted_log(self):
        self.logger.datasets_inserted += 1

    def increment_modified_log(self):
        self.logger.datasets_modified += 1

    def increment_skiped_log(self):
        self.logger.datasets_skiped += 1
