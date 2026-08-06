# LUH Profile Mapping - Custom Functions Documentation

This document describes the custom transformation functions required for the LUH profile mapping file (`LUH_profile_mapping.json`).

## Required Custom Functions

The following custom functions need to be implemented and registered with the MetadataParser when using the LUH profile mapping:

### 1. `splitAuthors`

**Purpose**: Split the author string (which may contain multiple authors separated by commas or semicolons) into individual author objects with firstName, lastName, and givenName fields.

**Input**: String like `"Knoop, H., F. Ament, B. Maronga"`

**Output**: 
- Sets `author` to first author: `"Knoop, H."`
- Sets `familyName` to `"Knoop"`
- Sets `givenName` to `"H."`
- Sets `extra_authors` array with remaining authors:
  ```python
  [
      {"extra_author": "Ament, F.", "familyName": "Ament", "givenName": "F."},
      {"extra_author": "Maronga, B.", "familyName": "Maronga", "givenName": "B."}
  ]
  ```

**Implementation Note**: This function should parse the author string, split by commas or semicolons, and extract first and last names. The first author is treated specially, with subsequent authors going into the `extra_authors` array.

### 2. `boolToString`

**Purpose**: Convert boolean values to lowercase string representation.

**Input**: `True` or `False`

**Output**: `"true"` or `"false"`

**Example**:
```python
def boolToString(value, params):
    if isinstance(value, bool):
        return str(value).lower()
    return value
```

### 3. `invertBoolean`

**Purpose**: Invert boolean value and return as boolean (not string). In the LUH case, `isopen=True` in source should become `isopen=False` in target.

**Input**: `True` or `False`

**Output**: `False` or `True`

**Example**:
```python
def invertBoolean(value, params):
    if isinstance(value, bool):
        return not value
    return value
```

### 4. `addPrefix`

**Purpose**: Add a prefix to a string value.

**Parameters**: 
- `prefix`: The prefix to add (e.g., `"luh-"`)

**Input**: `"a-generic-gust-definition-and-detection-method-based-on-wavelet-analysis"`

**Output**: `"luh-a-generic-gust-definition-and-detection-method-based-on-wavelet-analysis"`

**Example**:
```python
def addPrefix(value, params):
    prefix = params.get("prefix", "")
    return f"{prefix}{value}"
```

### 5. `buildLUHUrl`

**Purpose**: Build the full LUH dataset URL from the dataset name.

**Parameters**:
- `domain`: The base domain (e.g., `"https://data.uni-hannover.de"`)

**Input**: `"a-generic-gust-definition-and-detection-method-based-on-wavelet-analysis"`

**Output**: `"https://data.uni-hannover.de/dataset/a-generic-gust-definition-and-detection-method-based-on-wavelet-analysis"`

**Example**:
```python
def buildLUHUrl(value, params):
    domain = params.get("domain", "https://data.uni-hannover.de")
    return f"{domain}/dataset/{value}"
```

### 6. `setConstant`

**Purpose**: Set a constant value regardless of the source value.

**Parameters**:
- `value`: The constant value to set

**Input**: Any value (ignored)

**Output**: The constant value from parameters

**Example**:
```python
def setConstant(value, params):
    return params.get("value", "")
```

### 7. `excludeDownloadAll`

**Purpose**: Filter function to exclude resources that are "download all" packages (identified by having `downloadall_datapackage_hash` or `downloadall_metadata_modified` fields).

**Input**: Resource object

**Output**: `True` if resource should be included, `False` if it should be excluded

**Example**:
```python
def excludeDownloadAll(resource, params):
    # Exclude resources with downloadall fields
    if "downloadall_datapackage_hash" in resource or "downloadall_metadata_modified" in resource:
        return False
    return True
```

## Usage Example

```python
from metadata_parser import MetadataParser
import json

# Define custom functions
def splitAuthors(value, params):
    # Implementation here
    pass

def boolToString(value, params):
    return str(value).lower() if isinstance(value, bool) else value

def invertBoolean(value, params):
    return not value if isinstance(value, bool) else value

def addPrefix(value, params):
    prefix = params.get("prefix", "")
    return f"{prefix}{value}"

def buildLUHUrl(value, params):
    domain = params.get("domain", "https://data.uni-hannover.de")
    return f"{domain}/dataset/{value}"

def setConstant(value, params):
    return params.get("value", "")

def excludeDownloadAll(resource, params):
    if "downloadall_datapackage_hash" in resource or "downloadall_metadata_modified" in resource:
        return False
    return True

# Load mapping configuration
with open("mappings/LUH_profile_mapping.json", "r") as f:
    mapping_config = json.load(f)

# Register custom functions
custom_functions = {
    "splitAuthors": splitAuthors,
    "boolToString": boolToString,
    "invertBoolean": invertBoolean,
    "addPrefix": addPrefix,
    "buildLUHUrl": buildLUHUrl,
    "setConstant": setConstant,
    "excludeDownloadAll": excludeDownloadAll
}

# Create parser with custom functions
parser = MetadataParser(mapping_config, custom_functions=custom_functions)

# Parse the data
source_data = luh_api_package_show_with_results  # Your source data
result = parser.metadata_parser(source_data)
```

## Notes

1. The `splitAuthors` function is the most complex and requires careful implementation to handle various author name formats.
2. The `excludeDownloadAll` function is used as a filter in the resources section to exclude automatically generated "download all" resources.
3. Some fields in the expected output (like `metadata_created`, `metadata_modified`, `creator_user_id`) are set to empty or specific values after mapping, which may require post-processing.
4. The mapping assumes the source data structure from the LUH CKAN API response.
