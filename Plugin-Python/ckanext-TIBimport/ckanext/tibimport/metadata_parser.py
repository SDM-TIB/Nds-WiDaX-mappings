"""
Metadata Parser Module

This module provides a class-based approach to parse JSON metadata files
using mapping configurations. It refactors the original procedural code
into an object-oriented design following best practices.
"""

from jsonpath_ng import parse
import re
from typing import Dict, List, Callable, Any, Optional


class MetadataParser:
    """
    A class to parse JSON metadata using mapping configurations.
    
    This class handles the transformation of source JSON data into a target
    schema based on mapping rules defined in a mapping file.
    """
    
    def __init__(self, mapping_config: Dict[str, Any], custom_functions: Optional[Dict[str, Callable]] = None):
        """
        Initialize the MetadataParser with a mapping configuration.
        
        Args:
            mapping_config: Dictionary containing the mapping configuration
                           loaded from a mapping file.
            custom_functions: Optional dictionary of custom transformation functions
                            to register at initialization.
        """
        self.mapping_config = mapping_config
        self._builtin_functions = self._get_builtin_functions()
        self._custom_functions = {}
        
        # Register custom functions if provided
        if custom_functions:
            self.register_custom_functions(custom_functions)
    
    def _get_builtin_functions(self) -> Dict[str, Callable]:
        """
        Get the built-in transformation functions.
        
        Returns:
            Dictionary mapping function names to their implementations.
        """
        return {
            "toLower": self._to_lower,
            "tagsToList": self._tags_to_list,
            "edxTagsToList": self._edx_tags_to_list,
            "osfApiStoragetoHtmlStorage": self._osf_api_storage_to_html_storage,
            "normalizeName": self._normalize_name,
            "doiLink": self._doi_link,
            "setConstant": self._setConstant,
            "capitalizeFirst": self._capitalizeFirst,
            "setConstantTrue": self._setConstant_True,
            "setConstantFalse": self._setConstant_False
        }
    
    def register_custom_functions(self, functions: Dict[str, Callable]) -> None:
        """
        Register custom transformation functions.
        
        Args:
            functions: Dictionary mapping function names to callable functions.
        """
        self._custom_functions.update(functions)
    
    def _execute_transformation(self, value: Any, func_config: Dict[str, Any]) -> Any:
        """
        Execute a transformation function on a value.
        
        Args:
            value: The value to transform.
            func_config: Configuration dictionary containing function name and parameters.
        
        Returns:
            The transformed value.
        """
        func_name = func_config.get("function")
        
        # Check custom functions first, then built-in functions
        if func_name in self._custom_functions:
            return self._custom_functions[func_name](value, func_config.get("parameters", {}))
        elif func_name in self._builtin_functions:
            return self._builtin_functions[func_name](value, func_config.get("parameters", {}))
        
        # If function not found, return value unchanged
        return value
    
    # Built-in transformation functions
    
    @staticmethod
    def _to_lower(value: str, params: Dict) -> str:
        """Convert string to lowercase."""
        return value.lower()
    
    @staticmethod
    def _tags_to_list(value: List[str], params: Dict) -> List[Dict[str, str]]:
        """Convert list of strings to list of dictionaries with 'name' key."""
        return [{"name": item} for item in value]
    
    @staticmethod
    def _edx_tags_to_list(value: List[Dict], params: Dict) -> List[Dict[str, str]]:
        """Extract 'name' field from list of dictionaries."""
        return [{"name": item["name"]} for item in value]
    
    @staticmethod
    def _osf_api_storage_to_html_storage(value: str, params: Dict) -> str:
        """Convert OSF API storage URL to HTML storage URL."""
        pattern = r"^https://api\.osf\.io/v2/nodes/([a-zA-Z0-9]+)/files/.*$"
        replacement = r"https://osf.io/\1/files"
        formatted_url = re.sub(pattern, replacement, value)
        
        if formatted_url == value:
            return "Error: URL did not match expected API format."
        
        return formatted_url
    
    @staticmethod
    def _normalize_name(value: str, params: Dict) -> str:
        """Normalize name from 'Last, First' to 'First Last' format."""
        if "," in value:
            parts = value.split(",")
            return f"{parts[1].strip()} {parts[0].strip()}"
        return value
    
    @staticmethod
    def _doi_link(value: str, params: Dict) -> str:
        """Convert DOI to full DOI link."""
        return f"https://doi.org/{value}"
    
    @staticmethod
    def _setConstant(value: str, params: Dict) -> str:
        return params.get("value", "")

    @staticmethod
    def _setConstant_True(value: str, params: Dict) -> str:
        return True

    @staticmethod
    def _setConstant_False(value: str, params: Dict) -> str:
        return False

    @staticmethod
    def _capitalizeFirst(value: str, params: Dict) -> str:
        if value:
            return value.capitalize()
        return value
    
    def _extract_value(self, source_data: Dict, jsonpath: str) -> List[Any]:
        """
        Extract values from source data using JSONPath.
        
        Args:
            source_data: The source data dictionary.
            jsonpath: JSONPath expression to extract values.
        
        Returns:
            List of extracted values.
        """
        jsonpath_expr = parse(jsonpath)
        return [match.value for match in jsonpath_expr.find(source_data)]
    
    def _process_authors(self, row: Dict, property_config: Dict) -> Dict[str, Any]:
        """
        Process authors property with special handling for first author.
        
        Args:
            row: The current dataset row.
            property_config: Configuration for the authors property.
        
        Returns:
            Dictionary with 'author' and optionally 'extra_authors' keys.
        """
        result = {}
        matches = self._extract_value(row, property_config["source_property"])
        
        if not matches:
            result["author"] = ""
            return result
        
        # Process first author
        first_author = matches[0]
        if "transformation_function" in property_config:
            first_author = self._execute_transformation(
                first_author, 
                property_config["transformation_function"]
            )
        result["author"] = first_author
        
        # Process additional authors
        if len(matches) > 1:
            result["extra_authors"] = []
            for author in matches[1:]:
                if "transformation_function" in property_config:
                    author = self._execute_transformation(
                        author, 
                        property_config["transformation_function"]
                    )
                result["extra_authors"].append(author)
        
        return result
    
    def _process_keywords(self, row: Dict, property_config: Dict) -> Optional[List[Any]]:
        """
        Process keywords property.
        
        Args:
            row: The current dataset row.
            property_config: Configuration for the keywords property.
        
        Returns:
            List of keywords or None if no matches found.
        """
        matches = self._extract_value(row, property_config["source_property"])
        
        if len(matches) <= 1:
            return None
        
        keywords = []
        for value in matches:
            if "transformation_function" in property_config:
                value = self._execute_transformation(
                    value, 
                    property_config["transformation_function"]
                )
            keywords.append(value)
        
        return keywords
    
    def _process_standard_property(self, row: Dict, property_config: Dict) -> Optional[Any]:
        """
        Process a standard property.
        
        Args:
            row: The current dataset row.
            property_config: Configuration for the property.
        
        Returns:
            The processed value or None if no match found.
        """
        matches = self._extract_value(row, property_config["source_property"])
        
        if not matches or matches[0] is None:
            return None
        
        value = matches[0]
        if "transformation_function" in property_config:
            value = self._execute_transformation(
                value, 
                property_config["transformation_function"]
            )
        
        return value
    
    def _process_resources(self, row: Dict) -> List[Dict[str, Any]]:
        """
        Process resources for a dataset.
        
        Args:
            row: The current dataset row.
        
        Returns:
            List of processed resources.
        """
        resources_config = self.mapping_config.get("resources")
        if not resources_config:
            return []
        
        resources = []
        
        # Get resource list
        if resources_config["iterator"] == "":
            resources_list = [row]
        else:
            resources_list = self._extract_value(row, resources_config["iterator"])
        
        # Process each resource
        for resource in resources_list:
            ldm_resource = {}
            
            for resource_property in resources_config["properties"]:
                matches = self._extract_value(
                    resource, 
                    resource_property["source_resource_property"]
                )
                
                if matches and matches[0] is not None:
                    value = matches[0]
                    if "transformation_function" in resource_property:
                        value = self._execute_transformation(
                            value, 
                            resource_property["transformation_function"]
                        )
                    ldm_resource[resource_property["ldm_resource_property"]] = value
            
            resources.append(ldm_resource)
        
        return resources
    
    def metadata_parser(self, source_data: Dict) -> List[Dict[str, Any]]:
        """
        Parse metadata from source data using the configured mapping.
        
        Args:
            source_data: The source JSON data as a Python dictionary.
        
        Returns:
            List of parsed datasets as dictionaries.
        """
        ldm_list_datasets = []
        
        # Extract datasets using iterator
        datasets = self._extract_value(source_data, self.mapping_config["iterator"])
        
        # Process each dataset
        for row in datasets:
            ldm_dataset = {}
            
            # Process each property
            for property_config in self.mapping_config["properties"]:
                ldm_property = property_config["ldm_property"]
                
                # Handle special cases
                # if ldm_property == "authors":
                #     author_data = self._process_authors(row, property_config)
                #     ldm_dataset.update(author_data)
                
                if ldm_property == "keywords":
                    keywords = self._process_keywords(row, property_config)
                    if keywords:
                        ldm_dataset["keywords"] = keywords
                
                else:
                    value = self._process_standard_property(row, property_config)
                    
                    if value is not None:
                        ldm_dataset[ldm_property] = value
                        
            # Process resources if configured
            resources = self._process_resources(row)
            if resources:
                ldm_dataset["resources"] = resources
            
            # Only add dataset if it has required fields
            # if ldm_dataset.get("author") and ldm_dataset.get("title"):
            #     ldm_list_datasets.append(ldm_dataset)
            ldm_list_datasets.append(ldm_dataset)
        
        return ldm_list_datasets
