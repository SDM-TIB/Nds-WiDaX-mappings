# Göttingen Profile Mapping - Custom Functions Documentation

This document describes the custom transformation functions required for the Göttingen profile mapping file (`GOE_profile_mapping.json`).

## Required Custom Functions

The following custom functions need to be implemented and registered with the MetadataParser when using the Göttingen profile mapping:

### 1. `processGoettingenAuthors`

**Purpose**: Process the creator array from Göttingen's schema.org format and extract author information including extra authors.

**Input**: The full dataset dictionary (the function needs access to the `creator` array)

**Output**: 
- Sets `author` to first creator's name: `"Khan, Sarah"`
- Sets `familyName` to `"Khan"`
- Sets `givenName` to `"Sarah"`
- Sets `orcid` to `""` (empty string)
- If multiple creators exist, sets `extra_authors` array:
  ```python
  [
      {"extra_author": "Doe, John", "familyName": "Doe", "givenName": "John", "orcid": ""}
  ]
  ```

**Implementation Note**: This function should iterate through the `creator` array from the source data. The first creator becomes the main author, and subsequent creators go into `extra_authors`.

**Example**:
```python
def processGoettingenAuthors(value, params):
    """
    Process Göttingen creators array.
    value parameter contains the first creator's name
    but we need to access the full creator array from the source
    """
    # This function needs special handling in the parser
    # to pass the full dataset context
    pass
```

### 2. `extractDOI`

**Purpose**: Extract the DOI from a full DOI URL.

**Input**: `"https://doi.org/10.25625/EDOTRQ"`

**Output**: `"10.25625/EDOTRQ"`

**Example**:
```python
def extractDOI(value, params):
    if value and "doi.org/" in value:
        return value.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return value
```

### 3. `extractYear`

**Purpose**: Extract the year from a date string.

**Input**: `"2021-03-08"` or `"2021"`

**Output**: `"2021"`

**Example**:
```python
def extractYear(value, params):
    if value and "-" in value:
        return value.split("-")[0]
    return value
```

### 4. `buildGoettingenName`

**Purpose**: Build the dataset name from the DOI identifier following Göttingen's naming convention.

**Input**: `"https://doi.org/10.25625/EDOTRQ"`

**Output**: `"goe-doi-10-25625-edotrq"`

**Example**:
```python
import urllib.parse

def buildGoettingenName(value, params):
    # Extract DOI
    doi = value.replace("https://doi.org/", "").replace("http://doi.org/", "")
    
    # Build name with prefix
    name = "DOI-" + doi
    
    # Replace special characters
    special_chars = " /."
    for char in special_chars:
        name = name.replace(char, '-')
    
    name = name.lower()
    name = "goe-" + name
    
    # CKAN limits name to 100 chars
    name = name[:100].strip()
    
    # URL encode
    name = urllib.parse.quote(name)
    
    return name
```

### 5. `capitalizeFirst`

**Purpose**: Capitalize the first letter of a string.

**Input**: `"test2"`

**Output**: `"Test2"`

**Example**:
```python
def capitalizeFirst(value, params):
    if value:
        return value.capitalize()
    return value
```

### 6. `getGoettingenOrganization`

**Purpose**: Return the Göttingen organization dictionary.

**Input**: Any value (ignored)

**Output**: Organization dictionary
```python
{
    "approval_status": "approved",
    "description": "Göttingen Research Online is an institutional repository for the publication of research data at the Göttingen Campus. It is managed by the Göttingen eResearch Alliance, a joint group of SUB and GWDG. .",
    "display_name": "GöttingenResearchOnline",
    "image_display_url": "logo-goettingen.png",
    "image_url": "logo-goettingen.png",
    "is_organization": True,
    "name": "goettingen",
    "state": "active",
    "title": "GöttingenResearchOnline",
    "type": "organization"
}
```

**Example**:
```python
def getGoettingenOrganization(value, params):
    return {
        "approval_status": "approved",
        "description": "Göttingen Research Online is an institutional repository for the publication of research data at the Göttingen Campus. It is managed by the Göttingen eResearch Alliance, a joint group of SUB and GWDG. .",
        "display_name": "GöttingenResearchOnline",
        "image_display_url": "logo-goettingen.png",
        "image_url": "logo-goettingen.png",
        "is_organization": True,
        "name": "goettingen",
        "state": "active",
        "title": "GöttingenResearchOnline",
        "type": "organization"
    }
```

### 7. `processPublisher`

**Purpose**: Convert publisher object to CKAN publishers array format.

**Input**: `{"@type": "Organization", "name": "GRO.data"}`

**Output**: `[{"publisher": "GRO.data"}]`

**Example**:
```python
def processPublisher(value, params):
    if isinstance(value, dict) and "name" in value:
        return [{"publisher": value["name"]}]
    return []
```

### 8. `processSubjectAreas`

**Purpose**: Convert subjects array to CKAN subject_areas format.

**Input**: `["Agricultural Sciences", "Social Sciences"]`

**Output**: 
```python
[
    {"subject_area_additional": "", "subject_area_name": "Agricultural Sciences"},
    {"subject_area_additional": "", "subject_area_name": "Social Sciences"}
]
```

**Example**:
```python
def processSubjectAreas(value, params):
    if not value:
        return []
    
    result = []
    for subject in value:
        result.append({
            "subject_area_additional": "",
            "subject_area_name": subject
        })
    return result
```

### 9. `processRelatedPublications`

**Purpose**: Convert publications array to CKAN related_identifiers format.

**Input**: 
```python
[{
    "citation": "Government of India, Ministry of...",
    "url": "http://fsi.nic.in/details.php?pgID=sb_64"
}]
```

**Output**: 
```python
[{
    "identifier": "http://fsi.nic.in/details.php?pgID=sb_64",
    "identifier_type": "URL",
    "relation_type": "IsPublishedIn",
    "title": "Government of India, Ministry of..."
}]
```

**Example**:
```python
def processRelatedPublications(value, params):
    if not value:
        return []
    
    result = []
    for pub in value:
        citation = pub.get("citation", "")
        url = pub.get("url", "")
        
        if url:
            result.append({
                "identifier": url,
                "identifier_type": "URL",
                "relation_type": "IsPublishedIn",
                "title": citation
            })
    
    return result
```

### 10. `processGoettingenKeywords`

**Purpose**: Convert keywords array to CKAN tags format, filtering out subjects and handling special separators.

**Input**: `["Social Sciences", "keyword1", "keyword2; keyword3"]`

**Context**: Also needs access to `subjects` array to filter them out

**Output**: 
```python
[
    {"display_name": "keyword1", "name": "keyword1", "state": "active", "vocabulary_id": None},
    {"display_name": "keyword2", "name": "keyword2", "state": "active", "vocabulary_id": None},
    {"display_name": "keyword3", "name": "keyword3", "state": "active", "vocabulary_id": None}
]
```

**Implementation Note**: This function needs to:
1. Filter out keywords that are in the subjects list
2. Handle semicolon (;) and comma (,) separated keywords
3. Handle middle dot (·) separated keywords
4. Clean tags to only include permitted characters
5. Ensure minimum length of 2 characters
6. Limit to 100 characters max

**Example**:
```python
def processGoettingenKeywords(value, params):
    if not value:
        return []
    
    # Get subjects to filter (needs to be passed in params or accessed from context)
    subjects = params.get("subjects", [])
    
    PERMITTED_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_. "
    
    tag_list = []
    
    for keyword in value:
        tag = keyword
        
        # Handle various separators
        tag = tag.replace(';', ',')
        tag = tag.replace('·', ',')
        
        if ',' in tag:
            for t in tag.split(','):
                t = _adjust_tag(t, PERMITTED_CHARS)
                if t and t not in subjects:
                    tag_list.append({
                        "display_name": t,
                        "name": t,
                        "state": "active",
                        "vocabulary_id": None
                    })
        else:
            tag = _adjust_tag(tag, PERMITTED_CHARS)
            if tag and tag.strip() not in subjects:
                tag_list.append({
                    "display_name": tag.strip(),
                    "name": tag.strip(),
                    "state": "active",
                    "vocabulary_id": None
                })
    
    return tag_list

def _adjust_tag(tag, permitted_chars):
    tag = tag.replace("/", "-")
    tag = "".join(c for c in tag if c in permitted_chars)
    tag = tag.strip()
    
    # Minimum length is 2
    if len(tag) < 2:
        return ''
    
    # Max length is 100
    return tag[:100]
```

### 11. `setConstant`

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

## Usage Example

```python
from metadata_parser import MetadataParser
import json

# Define custom functions
def processGoettingenAuthors(value, params):
    # Implementation here
    pass

def extractDOI(value, params):
    if value and "doi.org/" in value:
        return value.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return value

def extractYear(value, params):
    if value and "-" in value:
        return value.split("-")[0]
    return value

# ... implement other functions

# Load mapping configuration
with open("mappings/GOE_profile_mapping.json", "r") as f:
    mapping_config = json.load(f)

# Register custom functions
custom_functions = {
    "processGoettingenAuthors": processGoettingenAuthors,
    "extractDOI": extractDOI,
    "extractYear": extractYear,
    "buildGoettingenName": buildGoettingenName,
    "capitalizeFirst": capitalizeFirst,
    "getGoettingenOrganization": getGoettingenOrganization,
    "processPublisher": processPublisher,
    "processSubjectAreas": processSubjectAreas,
    "processRelatedPublications": processRelatedPublications,
    "processGoettingenKeywords": processGoettingenKeywords,
    "setConstant": setConstant
}

# Create parser with custom functions
parser = MetadataParser(mapping_config, custom_functions=custom_functions)

# Parse the data
source_data = goettingen_dataset_parsed_to_dict  # Your source data
result = parser.metadata_parser(source_data)
```

## Notes

1. The `processGoettingenAuthors` function is complex and requires access to the full creator array from the source data, not just the first creator's name.
2. The `processGoettingenKeywords` function needs access to the subjects array to filter them out from keywords.
3. Tag processing includes special handling for various separators (semicolon, comma, middle dot).
4. The mapping assumes the source data structure from Göttingen's schema.org export format.
5. No resources are mapped as Göttingen datasets typically don't include file resources in the initial import.
