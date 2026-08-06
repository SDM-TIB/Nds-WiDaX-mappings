"""
Example Usage of MetadataParser Class

This script demonstrates how to use the MetadataParser class to parse
JSON metadata files using mapping configurations.
"""

import json
from metadata_parser import MetadataParser


def custom_uppercase_function(value, params):
    """Example custom function to convert text to uppercase."""
    return value.upper()


def custom_add_prefix_function(value, params):
    """Example custom function to add a prefix to a value."""
    prefix = params.get("prefix", "")
    return f"{prefix}{value}"


def main():
    # Example 1: Basic usage with the inspire_extended.json mapping
    print("=" * 80)
    print("Example 1: Basic Usage")
    print("=" * 80)
    
    # Load mapping configuration
    with open("input_inspirehep.json", "r") as f:
        mapping_config = json.load(f)
    
    # Load source data
    with open(mapping_config["source"], "r") as f:
        source_data = json.load(f)
    
    # Create parser instance
    parser = MetadataParser(mapping_config)
    
    # Parse metadata
    parsed_datasets = parser.metadata_parser(source_data)
    
    # Display results
    print(f"\nParsed {len(parsed_datasets)} datasets")
    if parsed_datasets:
        print("\nFirst dataset:")
        print(json.dumps(parsed_datasets[0], indent=2))
    
    # Example 2: Using custom functions at initialization
    print("\n" + "=" * 80)
    print("Example 2: Using Custom Functions at Initialization")
    print("=" * 80)
    
    # Define custom functions
    custom_functions = {
        "toUpperCase": custom_uppercase_function,
        "addPrefix": custom_add_prefix_function
    }
    
    # Create parser instance with custom functions
    parser2 = MetadataParser(mapping_config, custom_functions=custom_functions)
    
    # Parse with custom functions
    parsed_datasets_custom = parser2.metadata_parser(source_data)
    
    print(f"\nParsed {len(parsed_datasets_custom)} datasets with custom functions")
    
    # Example 3: Registering custom functions after initialization
    print("\n" + "=" * 80)
    print("Example 3: Registering Custom Functions After Initialization")
    print("=" * 80)
    
    parser3 = MetadataParser(mapping_config)
    
    # Register custom functions after creating the parser
    parser3.register_custom_functions({
        "toUpperCase": custom_uppercase_function
    })
    
    # Parse metadata
    parsed_datasets_registered = parser3.metadata_parser(source_data)
    
    print(f"\nParsed {len(parsed_datasets_registered)} datasets")
    
    # Example 4: Save output to file
    print("\n" + "=" * 80)
    print("Example 4: Save Output to File")
    print("=" * 80)
    
    output_file = "output_metadata.json"
    with open(output_file, "w") as f:
        json.dump(parsed_datasets, f, indent=4)
    
    print(f"\nOutput saved to {output_file}")
    
    # Example 5: Using a different mapping file (if available)
    print("\n" + "=" * 80)
    print("Example 5: Processing Multiple Mapping Files")
    print("=" * 80)
    
    # You can create multiple parser instances for different mappings
    # This demonstrates the flexibility of the class-based approach
    
    print("\nYou can create multiple MetadataParser instances for different mappings:")
    print("parser_inspire = MetadataParser(inspire_mapping, custom_functions=inspire_funcs)")
    print("parser_datacite = MetadataParser(datacite_mapping, custom_functions=datacite_funcs)")
    print("parser_custom = MetadataParser(custom_mapping)")


if __name__ == "__main__":
    main()
