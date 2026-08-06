"""
Unit Tests for MetadataParser Class

This module contains unit tests for the MetadataParser class to ensure
all functionality works as expected.
"""

import unittest
import json
from metadata_parser import MetadataParser


class TestMetadataParser(unittest.TestCase):
    """Test cases for MetadataParser class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.simple_mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {"source_property": "title", "ldm_property": "title"},
                {"source_property": "description", "ldm_property": "description"}
            ]
        }
        
        self.simple_data = {
            "data": [
                {"title": "Test Dataset 1", "description": "Description 1"},
                {"title": "Test Dataset 2", "description": "Description 2"}
            ]
        }
    
    def test_basic_parsing(self):
        """Test basic parsing without transformations."""
        parser = MetadataParser(self.simple_mapping)
        result = parser.metadata_parser(self.simple_data)
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Test Dataset 1")
        self.assertEqual(result[1]["description"], "Description 2")
    
    def test_to_lower_transformation(self):
        """Test toLower transformation function."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "title",
                    "ldm_property": "title",
                    "transformation_function": {
                        "function": "toLower",
                        "parameters": {}
                    }
                }
            ]
        }
        
        data = {"data": [{"title": "UPPERCASE TITLE"}]}
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["title"], "uppercase title")
    
    def test_normalize_name_transformation(self):
        """Test normalizeName transformation function."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "author",
                    "ldm_property": "authors",
                    "transformation_function": {
                        "function": "normalizeName",
                        "parameters": {}
                    }
                }
            ]
        }
        
        data = {"data": [{"author": "Doe, John", "title": "Test"}]}
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["author"], "John Doe")
    
    def test_doi_link_transformation(self):
        """Test doiLink transformation function."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "doi",
                    "ldm_property": "doi_link",
                    "transformation_function": {
                        "function": "doiLink",
                        "parameters": {}
                    }
                },
                {"source_property": "title", "ldm_property": "title"}
            ]
        }
        
        data = {"data": [{"doi": "10.1234/test", "title": "Test"}]}
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["doi_link"], "https://doi.org/10.1234/test")
    
    def test_tags_to_list_transformation(self):
        """Test tagsToList transformation function."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "tags",
                    "ldm_property": "keywords",
                    "transformation_function": {
                        "function": "tagsToList",
                        "parameters": {}
                    }
                }
            ]
        }
        
        data = {"data": [{"tags": ["tag1", "tag2"], "title": "Test"}]}
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        expected = [{"name": "tag1"}, {"name": "tag2"}]
        self.assertEqual(result[0]["keywords"], expected)
    
    def test_authors_processing(self):
        """Test special authors processing with multiple authors."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "authors[*]",
                    "ldm_property": "authors",
                    "transformation_function": {
                        "function": "normalizeName",
                        "parameters": {}
                    }
                },
                {"source_property": "title", "ldm_property": "title"}
            ]
        }
        
        data = {
            "data": [{
                "authors": ["Doe, John", "Smith, Jane", "Brown, Bob"],
                "title": "Test Paper"
            }]
        }
        
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["author"], "John Doe")
        self.assertEqual(len(result[0]["extra_authors"]), 2)
        self.assertEqual(result[0]["extra_authors"][0], "Jane Smith")
        self.assertEqual(result[0]["extra_authors"][1], "Bob Brown")
    
    def test_keywords_processing(self):
        """Test keywords processing with multiple values."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "tags[*]",
                    "ldm_property": "keywords"
                },
                {"source_property": "title", "ldm_property": "title"}
            ]
        }
        
        data = {
            "data": [{
                "tags": ["keyword1", "keyword2", "keyword3"],
                "title": "Test"
            }]
        }
        
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(len(result[0]["keywords"]), 3)
        self.assertIn("keyword1", result[0]["keywords"])
    
    def test_resources_processing(self):
        """Test resources processing."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {"source_property": "title", "ldm_property": "title"}
            ],
            "resources": {
                "iterator": "files[*]",
                "properties": [
                    {"source_resource_property": "name", "ldm_resource_property": "title"},
                    {"source_resource_property": "url", "ldm_resource_property": "access_url"}
                ]
            }
        }
        
        data = {
            "data": [{
                "title": "Test Dataset",
                "files": [
                    {"name": "File 1", "url": "http://example.com/file1"},
                    {"name": "File 2", "url": "http://example.com/file2"}
                ]
            }]
        }
        
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(len(result[0]["resources"]), 2)
        self.assertEqual(result[0]["resources"][0]["title"], "File 1")
        self.assertEqual(result[0]["resources"][1]["access_url"], "http://example.com/file2")
    
    def test_custom_function(self):
        """Test custom transformation function at initialization."""
        def custom_uppercase(value, params):
            return value.upper()
        
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "title",
                    "ldm_property": "title",
                    "transformation_function": {
                        "function": "customUppercase",
                        "parameters": {}
                    }
                }
            ]
        }
        
        data = {"data": [{"title": "lowercase title"}]}
        
        custom_functions = {"customUppercase": custom_uppercase}
        parser = MetadataParser(mapping, custom_functions=custom_functions)
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["title"], "LOWERCASE TITLE")
    
    def test_register_custom_functions(self):
        """Test registering custom functions separately."""
        def custom_reverse(value, params):
            return value[::-1]
        
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {
                    "source_property": "text",
                    "ldm_property": "text",
                    "transformation_function": {
                        "function": "reverseText",
                        "parameters": {}
                    }
                },
                {"source_property": "title", "ldm_property": "title"}
            ]
        }
        
        data = {"data": [{"text": "hello", "title": "Test"}]}
        parser = MetadataParser(mapping)
        parser.register_custom_functions({"reverseText": custom_reverse})
        
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["text"], "olleh")
    
    def test_missing_required_fields(self):
        """Test that datasets without required fields are filtered out."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {"source_property": "title", "ldm_property": "title"}
            ]
        }
        
        # Dataset without author and title should be filtered
        data = {"data": [{"description": "No title or author"}]}
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(len(result), 0)
    
    def test_jsonpath_extraction(self):
        """Test JSONPath extraction with nested data."""
        mapping = {
            "iterator": "$.results[*]",
            "properties": [
                {"source_property": "metadata.title", "ldm_property": "title"},
                {"source_property": "metadata.author.name", "ldm_property": "authors"}
            ]
        }
        
        data = {
            "results": [{
                "metadata": {
                    "title": "Nested Title",
                    "author": {"name": "John Doe"}
                }
            }]
        }
        
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        self.assertEqual(result[0]["title"], "Nested Title")
        self.assertEqual(result[0]["author"], "John Doe")
    
    def test_empty_data(self):
        """Test parsing with empty data."""
        parser = MetadataParser(self.simple_mapping)
        result = parser.metadata_parser({"data": []})
        
        self.assertEqual(len(result), 0)
    
    def test_none_values(self):
        """Test handling of None values."""
        mapping = {
            "iterator": "$.data[*]",
            "properties": [
                {"source_property": "title", "ldm_property": "title"},
                {"source_property": "optional_field", "ldm_property": "optional"}
            ]
        }
        
        data = {"data": [{"title": "Test", "optional_field": None}]}
        parser = MetadataParser(mapping)
        result = parser.metadata_parser(data)
        
        # None values should not be included in result
        self.assertNotIn("optional", result[0])


def run_tests():
    """Run all tests."""
    unittest.main()


if __name__ == "__main__":
    run_tests()
