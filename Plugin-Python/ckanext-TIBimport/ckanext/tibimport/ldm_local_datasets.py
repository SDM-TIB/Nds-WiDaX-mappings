from ckan.plugins import toolkit
import ckan.plugins as plugins
import ckan.model as model
import ckan.logic as logic

import ckan.lib.plugins as lib_plugins
import ckan.lib.dictization.model_save as model_save
import ckan.lib.navl.dictization_functions as df
import six
import ckan.lib.dictization.model_dictize as model_dictize
from ckan.lib.search import rebuild, commit
import time
import traceback
            
from logging import getLogger
log = getLogger(__name__)

NotFound = logic.NotFound


class LDMLocalDatasets:

    def __init__(self):
                
        # CKAN's API Actions
        self.action_package_show = toolkit.get_action('package_show')
        # Allow unauthorized ejecution
        toolkit.auth_allow_anonymous_access(self.action_package_show)
    
    # LOCAL CKAN INTERACTION
    # **********************

    def get_LDM_dataset(self, name):
        # https://docs.ckan.org/en/2.9/api/#ckan.logic.action.get.package_show
        # Note: Returns data even with dataset deleted => ds['state'] = 'deleted'
        params = {'id': name}
        context = {'model': model, 'session': model.Session, 'ignore_auth': True, 'user': 'admin'}
        
        try:
            result = self.action_package_show(context, params)
        except NotFound as e:
            return {}
        return result
    
    def insert_ldm_dataset(self):

        pass

    def _custom_package_create(self, context, data_dict):
        """
        Custom package_create implementation using CKAN models.
        Based on CKAN's package_create but without automatic indexing.
        This allows defer_commit to work properly without automatic indexing
        by skipping the synchronous_search plugin notifications.
        """

        model = context['model']
        session = context['session']
        user = context.get('user')
        
        # Store original defer_commit value
        original_defer_commit = context.get('defer_commit', False)
        
        # CRITICAL: Set defer_commit to prevent synchronous_search from indexing
        # The synchronous_search plugin checks if there are uncommitted changes
        # By deferring commit, we prevent it from triggering indexing on each insert
        context['defer_commit'] = True
        
        # Set package type
        if 'type' not in data_dict:
            package_plugin = lib_plugins.lookup_package_plugin()
            try:
                package_type = package_plugin.package_types()[0]
            except (AttributeError, IndexError):
                package_type = 'dataset'
            data_dict['type'] = package_type
        else:
            package_plugin = lib_plugins.lookup_package_plugin(data_dict['type'])
        
        # Get schema
        if 'schema' in context:
            schema = context['schema']
        else:
            schema = package_plugin.create_package_schema()
        
        # Validate
        data, errors = lib_plugins.plugin_validate(
            package_plugin, context, data_dict, schema, 'package_create')
        
        if errors:
            model.Session.rollback()
            raise logic.ValidationError(errors)
        
        # Add creator user id
        if user:
            user_obj = model.User.by_name(six.ensure_text(user))
            if user_obj:
                data['creator_user_id'] = user_obj.id
        
        # Save package using CKAN's model_save
        pkg = model_save.package_dict_save(data, context)
        
        # Flush to get IDs
        model.Session.flush()
        data['id'] = pkg.id
        if data.get('resources'):
            for index, resource in enumerate(data['resources']):
                resource['id'] = pkg.resources[index].id
        
        # Handle owner_org
        context_org_update = context.copy()
        context_org_update['ignore_auth'] = True
        context_org_update['defer_commit'] = True
        toolkit.get_action('package_owner_org_update')(
            context_org_update,
            {'id': pkg.id, 'organization_id': pkg.owner_org}
        )
        
        # Call plugin hooks for IPackageController
        # Note: We call these to maintain compatibility with other plugins
        for item in plugins.PluginImplementations(plugins.IPackageController):
            try:
                item.create(pkg)
            except AttributeError:
                pass  # Plugin doesn't implement create hook
        
        for item in plugins.PluginImplementations(plugins.IPackageController):
            try:
                item.after_create(context, data)
            except AttributeError:
                pass  # Plugin doesn't implement after_create hook
        
        # Create activity if not private
        if not pkg.private:
            user_obj = model.User.by_name(user)
            if user_obj:
                user_id = user_obj.id
            else:
                user_id = 'not logged in'
            
            activity = pkg.activity_stream_item('new', user_id)
            session.add(activity)
        
        # IMPORTANT: We keep defer_commit=True throughout to prevent automatic indexing
        # The synchronous_search plugin triggers on model.Session.commit() via IDomainObjectModification
        # By not committing here, we prevent individual package indexing
        # The caller (batch_package_create) will commit all at once, then rebuild_index_missing
        # can be called to index all packages efficiently in a batch
        
        # Only commit if the ORIGINAL context requested immediate commit
        if not original_defer_commit and not context.get('defer_commit'):
            model.Session.commit()
        
        # Return package id or dict based on context
        return_id_only = context.get('return_id_only', False)
        if return_id_only:
            return pkg.id
        
        # Return the package dict
        return data
    
    def _custom_package_update(self, context, data_dict):
        """
        Custom package_update implementation using CKAN models.
        Based on CKAN's package_update but without plugin notifications.
        This allows defer_commit to work properly without automatic indexing.
        """
        
        model = context['model']
        session = context['session']
        user = context.get('user')
        
        # Get the package id
        name_or_id = data_dict.get('id') or data_dict.get('name')
        if not name_or_id:
            raise logic.ValidationError({'id': 'Missing value'})
        
        # Get the existing package
        pkg = model.Package.get(name_or_id)
        if pkg is None:
            raise logic.NotFound('Package was not found.')
        
        # Set the package type
        if 'type' not in data_dict:
            data_dict['type'] = pkg.type
        
        # Get the plugin
        package_plugin = lib_plugins.lookup_package_plugin(data_dict['type'])
        
        # Check if we allow partial updates
        if context.get('allow_partial_update', False):
            # Get current package data to merge with
            old_data = model_dictize.package_dictize(pkg, context)
            old_data.update(data_dict)
            data_dict = old_data
        
        # Store the package for the context
        context['package'] = pkg
        data_dict['id'] = pkg.id
        
        # Get schema
        if 'schema' in context:
            schema = context['schema']
        else:
            schema = package_plugin.update_package_schema()
        
        # Validate
        data, errors = lib_plugins.plugin_validate(
            package_plugin, context, data_dict, schema, 'package_update')
        
        if errors:
            model.Session.rollback()
            raise logic.ValidationError(errors)
        
        # Save package using CKAN's model_save
        pkg = model_save.package_dict_save(data, context)
        
        # Flush to ensure IDs are available
        model.Session.flush()
        
        # Update resource IDs in data dict
        if data.get('resources'):
            for index, resource in enumerate(data['resources']):
                resource['id'] = pkg.resources[index].id
        
        # Handle owner_org update
        context_org_update = context.copy()
        context_org_update['ignore_auth'] = True
        context_org_update['defer_commit'] = True
        toolkit.get_action('package_owner_org_update')(
            context_org_update,
            {'id': pkg.id, 'organization_id': pkg.owner_org}
        )
        
        # Call plugin hooks selectively - exclude synchronous_search to avoid automatic indexing
        # This allows other plugins to do their work while preventing automatic indexing
        excluded_plugins = ['synchronous_search']
        for item in plugins.PluginImplementations(plugins.IPackageController):
            plugin_name = item.name if hasattr(item, 'name') else item.__class__.__name__
            if plugin_name not in excluded_plugins:
                try:
                    item.edit(pkg)
                except AttributeError:
                    pass  # Plugin doesn't implement edit hook
        
        for item in plugins.PluginImplementations(plugins.IPackageController):
            plugin_name = item.name if hasattr(item, 'name') else item.__class__.__name__
            if plugin_name not in excluded_plugins:
                try:
                    item.after_update(context, data)
                except AttributeError:
                    pass  # Plugin doesn't implement after_update hook
        
        # Create activity if not private
        if not pkg.private:
            user_obj = model.User.by_name(user) if user else None
            if user_obj:
                user_id = user_obj.id
            else:
                user_id = 'not logged in'
            
            activity = pkg.activity_stream_item('changed', user_id)
            session.add(activity)
        
        # Commit based on context['defer_commit'] setting
        if not context.get('defer_commit'):
            model.Session.commit()
        
        # Return package dict
        return_id_only = context.get('return_id_only', False)
        if return_id_only:
            return pkg.id
        
        return data

    def _rebuild_index_missing(self):
        
        index_start = time.time()
        
        try:
            log.info("Starting rebuild index with only_missing")
            
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
            log.info(f"custom_rebuild_missing: Rebuild index completed in {index_time:.2f}s")
            
        except Exception as e:
            log.error(f"Error during rebuild index: {str(e)}")
            log.error(traceback.format_exc())
        finally:
            pass
