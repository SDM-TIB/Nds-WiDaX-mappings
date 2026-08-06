# pytest --ckan-ini=test.ini ckanext/tibimport/tests/test_OSNADATA_profile.py -s

from ckanext.tibimport.OSNADATA_ParserProfile import OSNADATA_ParserProfile
from ckanext.tibimport.logic2 import LDM_DatasetImport
from ckanext.tibimport.oai_harvester import OAIHarvester, OAIError, OAIHarvestError
import pytest

from OSNADATA_Profile_Mocks import osnadata_dataset_parsed_to_dict, osnadata_dataset_parsed_to_ckan_dict, resumption_token_ok, \
    resumption_token_empty, expected_list_of_dict_from_osnadata, osnadata_all_datasets_list_response, \
    ckan_dict_of_imported_dataset, osnadata_dict_of_imported_dataset, local_organization_data, local_dataset_data, \
    OAIHarvester_parsed_record, OAIHarvester_parsed_record_enriched, OAIHarvester_mapping_ckan_dict

import sqlalchemy as sa

import xmltodict
from xml.etree import ElementTree

import pprint

import requests
from requests.models import Response
import json
from unittest.mock import Mock, patch, MagicMock
from ckan.plugins import toolkit

skip_test1 = False
skip_test_DatasetImport = True


# =============================================================================
# MOCK LAYER 1 — OAIHarvester._request
# =============================================================================
# Replaces urllib-based HTTP inside the harvester.
# Returns an ET.Element (exactly what the real _request returns) or raises
# OAIError / OAIHarvestError to simulate failures.
#
# Usage in tests:
#   obj.harvester.base_url = 'test_<scenario>'
#   @patch.object(OAIHarvester, '_request', side_effect=mocked_oai_request)
# =============================================================================

def mocked_oai_request(self_harvester, params):
    """
    Side-effect for @patch.object(OAIHarvester, '_request', ...).

    Dispatches on self_harvester.base_url so each test selects its fixture by
    setting   obj.harvester.base_url = 'test_<scenario>'.

    Mirrors real _request() behaviour:
      • returns a parsed ET.Element on success
      • calls _check_oai_error() so OAI <error> elements raise OAIError
      • raises OAIHarvestError for network/HTTP failures
    """
    base = self_harvester.base_url

    # OAI error response (e.g. noSetHierarchy)
    if base == 'test_osnadata_get_datasets_list_error':
        with open('./ckanext/tibimport/tests/osnadata_error.xml', 'r') as f:
            tree = ElementTree.fromstring(f.read())
        self_harvester._check_oai_error(tree)   # raises OAIError
        return tree                              # never reached

    # Single-page OK response (3 records, empty resumption token)
    elif base in ('test_osnadata_get_datasets_list_ok', 'test_get_all_datasets_dicts'):
        with open('./ckanext/tibimport/tests/osnadata_dataset.xml', 'r') as f:
            return ElementTree.fromstring(f.read())

    # Full response (45 records, no resumption token)
    elif base == 'test_osnadata_get_datasets_list_all':
        with open('./ckanext/tibimport/tests/osnadata_response.xml', 'r') as f:
            return ElementTree.fromstring(f.read())

    # HTTP 404 / network failure
    elif base == 'test_osnadata_get_datasets_list_no_response':
        raise OAIHarvestError("HTTP Error 404: Not Found")

    else:
        raise OAIHarvestError(f"mocked_oai_request: no fixture for base_url={base!r}")


# =============================================================================
# MOCK LAYER 2 — requests.get
# =============================================================================
# OAI-PMH calls no longer reach requests.get (they go through the harvester).
# This mock handles ONLY the citation/export API calls from
# _get_citations_and_license_from_API.
#
# Usage in tests:
#   obj.osnadata_export_to_dataverse_json_url = 'test_osnadata_export_dataset'
#   @patch('ckanext.tibimport.OSNADATA_ParserProfile.requests.get',
#          side_effect=mocked_requests_get)
# =============================================================================

def mocked_requests_get(*args, **kwargs):
    """Side-effect for requests.get — handles only export/citation API calls."""
    request_url = args[0]
    response = Response()
    response.status_code = 200

    if 'test_osnadata_export_dataset' in request_url:
        doi_id = request_url.replace("test_osnadata_export_datasetdoi:10.26249/FK2/", "")
        if doi_id not in ['48FTWW', '4B3NSO', '5AXRBJ']:
            doi_id = '48FTWW'
        with open(f'./ckanext/tibimport/tests/osnadata_export_{doi_id}.json', 'r') as f:
            response._content = f.read().encode()

    # _get_schema_report(None) fallback — not normally hit because get_datasets_list
    # passes the ET tree directly; kept for safety in case of direct schema calls.
    elif request_url in ('test_osnadata_get_datasets_list_ok',
                         'test_get_all_datasets_dicts',
                         'test_osnadata_get_datasets_list_all'):
        with open('./ckanext/tibimport/tests/osnadata_dataset.xml', 'r') as f:
            response._content = f.read().encode()

    else:
        raise AssertionError(f"mocked_requests_get: unexpected URL {request_url!r}")

    return response


# =============================================================================
# Convenience decorator: stacks both patches for tests that need citation data.
#
# Argument order in decorated function:
#   def test_xxx(mock_oai, mock_get): ...
# =============================================================================

def patch_both(func):
    func = patch(
        'ckanext.tibimport.OSNADATA_ParserProfile.requests.get',
        side_effect=mocked_requests_get
    )(func)
    func = patch.object(
        OAIHarvester, '_request',
        autospec=True,                  # passes `self` (the harvester instance) to the side_effect
        side_effect=mocked_oai_request
    )(func)
    return func


# =============================================================================
# TEST LDM_DatasetImport  
# =============================================================================

@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_org_not_found():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    res_dict = obj.get_local_organization("TEST-3499-944kdf20")
    assert res_dict == {}


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_insert_local_org():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    org_dict = local_organization_data
    obj.insert_organization(org_dict)
    res_dict = obj.get_local_organization(org_dict['name'])
    print(res_dict)
    assert res_dict['name'] == org_dict['name']


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_org_found():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    res_dict = obj.get_local_organization(local_organization_data['name'])
    assert res_dict['name'] == local_organization_data['name']


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_dataset_not_found():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    assert obj.get_local_dataset("TEST-3499-944kdf20") == {}


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_insert_local_dataset():
    db_url = toolkit.config['sqlalchemy.url']
    engine = sa.create_engine(db_url)
    engine.execute(u"DELETE FROM package_extra WHERE package_id = '476cdf71-1048-4a6f-a28a-58fff547dae5'")
    engine.execute(u"DELETE FROM package WHERE id = '476cdf71-1048-4a6f-a28a-58fff547dae5'")

    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    ds_dict = local_dataset_data
    obj.insert_dataset(ds_dict)
    res_dict = obj.get_local_dataset(ds_dict['name'])
    ds_dict['metadata_created'] = res_dict['metadata_created']
    ds_dict['metadata_modified'] = res_dict['metadata_modified']
    print(res_dict)
    assert res_dict['name'] == ds_dict['name']


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_dataset_found():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    ds_dict = local_dataset_data
    res_dict = obj.get_local_dataset(local_dataset_data['name'])
    ds_dict['metadata_created'] = res_dict['metadata_created']
    ds_dict['metadata_modified'] = res_dict['metadata_modified']
    ds_dict['state'] = res_dict['state']
    assert res_dict['name'] == ds_dict['name']


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_update_local_dataset():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    ds_dict = local_dataset_data
    ds_dict['author'] = "New Test Author"
    obj.update_dataset(ds_dict)
    res_dict = obj.get_local_dataset(local_dataset_data['name'])
    assert res_dict['author'] == ds_dict['author']


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_delete_local_dataset():
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    obj.delete_dataset(local_dataset_data['name'])
    res_dict = obj.get_local_dataset(local_dataset_data['name'])
    assert res_dict['state'] == 'deleted'


@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_delete_local_org(app):
    parser = OSNADATA_ParserProfile()
    obj = LDM_DatasetImport(parser)
    obj.delete_organization(local_organization_data['name'])
    assert obj.get_local_organization(local_organization_data['name']) == {}


# =============================================================================
# TEST osnaData live connection test (real HTTP)
# =============================================================================

def test_osnadata_harvesting_remote_ok():
    parser = OSNADATA_ParserProfile()
    url = parser.osnaData_ListRecords_url + parser.osnaData_metadata_schema_response_prefix
    response = requests.get(url)
    print('XML first page response:', response.content[-1000:], url)
    assert response.ok


# =============================================================================
# TEST get_datasets_list  — now mocks OAIHarvester._request instead of requests.get
# =============================================================================

@patch.object(OAIHarvester, '_request', autospec=True, side_effect=mocked_oai_request)
def test_osnadata_get_datasets_list_error(mock_request):
    """
    OAI response contains an <error> element.
    _request raises OAIError → get_datasets_list catches it and returns [].
    """
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_error'

    res_dict = obj.get_datasets_list()

    assert res_dict == []


@patch_both
def test_osnadata_get_datasets_list_ok(mock_oai, mock_get):
    """
    3-record fixture, empty resumption token, all records are open datasets.
    """
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_ok'
    obj.osnadata_export_to_dataverse_json_url = 'test_osnadata_export_dataset'

    res_dict = obj.get_datasets_list()

    assert 3 == len(res_dict)
    print('\n\nDATASETS LIST: ', res_dict)
    assert res_dict == expected_list_of_dict_from_osnadata


@patch_both
def test_osnadata_get_datasets_list_complete(mock_oai, mock_get):
    """
    Full response XML — only open Dataset records pass the filter (45 expected).
    """
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_all'
    obj.osnadata_export_to_dataverse_json_url = 'test_osnadata_export_dataset'

    res_dict = obj.get_datasets_list()

    assert 45 == len(res_dict)


@patch.object(OAIHarvester, '_request', autospec=True, side_effect=mocked_oai_request)
def test_osnadata_get_datasets_list_no_response(mock_request):
    """
    Network failure: _request raises OAIHarvestError → get_datasets_list returns [].
    """
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_no_response'

    res_dict = obj.get_datasets_list()

    print("RES DICT:\n", res_dict)
    assert res_dict == []


# =============================================================================
# TEST resumption-token helpers  
# =============================================================================

def test_get_osnadata_resumption_token_empty():
    obj = OSNADATA_ParserProfile()
    with open('./ckanext/tibimport/tests/osnadata_dataset.xml', 'r') as f:
        xml_tree_data = ElementTree.fromstring(f.read())
    resumption_token = obj._get_osnaData_resumption_token(xml_tree_data)
    assert resumption_token == resumption_token_empty


def test_get_osnadata_resumption_token_not_present():
    obj = OSNADATA_ParserProfile()
    with open('./ckanext/tibimport/tests/osnadata_dataset.xml', 'r') as f:
        xml_tree_data = ElementTree.fromstring(f.read())
    resumption_token = obj._get_osnaData_resumption_token(xml_tree_data)
    assert resumption_token == resumption_token_empty


# =============================================================================
# TEST XML → dict parsing  (unchanged — work directly on ET elements)
# =============================================================================

def test_parse_osnadata_XML_RECORD_to_DICT():
    parser = OSNADATA_ParserProfile()
    with open('./ckanext/tibimport/tests/osnadata_dataset.xml', 'r') as f:
        xml_tree_data = ElementTree.fromstring(f.read())

    ns0 = '{http://www.openarchives.org/OAI/2.0/}'
    for record in xml_tree_data.iter(ns0 + 'record'):
        res_dict = parser.parse_osnaData_XML_RECORD_to_DICT(record)
        break

    res = parser._get_citations_and_license_from_API(res_dict['header']['identifier'])
    res_dict['metadata']['osnaDataDataset']['citation'] = res['citation']
    res_dict['metadata']['osnaDataDataset']['license']  = res['license']

    print('\n\nDICT response:\n', res_dict)
    assert res_dict == osnadata_dataset_parsed_to_dict


def test_parse_osnadata_XML_RECORD_to_DICT_using_OAIHarvester():
    """
    parse_osnaData_XML_RECORD_to_DICT_using_OAIHarvester delegates to
    OAIHarvester.parse_record() and returns the OAIHarvester dict structure
    (not the hand-written OSNADATA-to-CKAN structure).
    Operates directly on a fixture <record> element — no HTTP mocking needed.
    """
    parser = OSNADATA_ParserProfile()
    with open('./ckanext/tibimport/tests/osnadata_dataset.xml', 'r') as f:
        xml_tree_data = ElementTree.fromstring(f.read())

    ns0 = '{http://www.openarchives.org/OAI/2.0/}'
    record_el = next(xml_tree_data.iter(ns0 + 'record'))

    result = parser.parse_osnaData_XML_RECORD_to_DICT_using_OAIHarvester(record_el)

    assert result == OAIHarvester_parsed_record
    # # Top-level keys match OAIHarvester format
    # assert set(result.keys()) == {'header', 'metadata'}

    # # Header
    # header = result['header']
    # assert header['identifier'] == 'doi:10.26249/FK2/48FTWW'
    # assert header['datestamp']  == '2024-07-12T00:00:00Z'
    # assert header['setSpecs']   == []
    # assert header['status']     is None

    # # Metadata top-level structure
    # meta = result['metadata']
    # assert meta['metadataFormat'] == 'oai_datacite'

    # # Identifier
    # assert meta['identifier']['value']          == '10.26249/FK2/48FTWW'
    # assert meta['identifier']['identifierType'] == 'DOI'

    # # Title
    # assert len(meta['titles']) >= 1
    # assert meta['titles'][0]['value'] == (
    #     'Cooperativity of cysteines governs GSDMD palmitoylation-mediated '
    #     'membrane targeting and assembly'
    # )

    # # Creators
    # assert len(meta['creators']) == 2
    # assert meta['creators'][0]['familyName'] == 'Margheritis'
    # assert meta['creators'][1]['familyName'] == 'Cosentino'

    # # Publication year and resource type
    # assert meta['publicationYear'] == '2024'
    # assert meta['resourceType']['resourceTypeGeneral'] == 'Dataset'

    # # Version
    # assert meta['version'] == '1.0'

    # # Related identifiers
    # assert len(meta['relatedIdentifiers']) == 2
    # assert meta['relatedIdentifiers'][0]['relationType'] == 'IsCitedBy'

    print('\n\nOAIHarvester parsed record:\n', result)


def test_parse_osnadata_RECORD_DICT_to_LDM_CKAN_DICT():
    parser = OSNADATA_ParserProfile()
    res_dict = parser.parse_osnaData_RECORD_DICT_to_LDM_CKAN_DICT(osnadata_dataset_parsed_to_dict)
    print("\n\nPARSED DICT: \n", res_dict)
    assert res_dict == osnadata_dataset_parsed_to_ckan_dict


# =============================================================================
# TEST get_all_datasets_dicts
# =============================================================================

@patch_both
def test_get_all_datasets_dicts(mock_oai, mock_get):
    """
    get_all_datasets_dicts → get_datasets_list → CKAN dict conversion.
    Uses the 3-record fixture and checks the full expected output.
    """
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_get_all_datasets_dicts'
    obj.osnadata_export_to_dataverse_json_url = 'test_osnadata_export_dataset'

    res_list = obj.get_all_datasets_dicts()

    print("\nALL Datasets List:\n", res_list)
    print("\nLEN:", len(res_list))
    assert res_list == osnadata_all_datasets_list_response


# =============================================================================
# TEST live full harvest (hits real API)
# =============================================================================

def test_all_datasets_are_retrieved():
    obj = OSNADATA_ParserProfile()
    res_list = obj.get_all_datasets_dicts()
    print("\nTOTAL OSNADATA DS:", len(res_list))
    assert len(res_list) == obj.total_osnaData_datasets


# =============================================================================
# TEST should_be_updated
# =============================================================================

def test_should_be_updated_false():
    obj = OSNADATA_ParserProfile()
    res = obj.should_be_updated(ckan_dict_of_imported_dataset, osnadata_dict_of_imported_dataset)
    assert res == False


def test_should_be_updated_true():
    obj = OSNADATA_ParserProfile()
    remote_dataset = osnadata_dict_of_imported_dataset
    remote_dataset['title'] = 'New Title'
    print("\n\nCKAN DATASET:\n", ckan_dict_of_imported_dataset)
    print("\n\nREMOTE DATASET:\n", remote_dataset)
    res = obj.should_be_updated(ckan_dict_of_imported_dataset, remote_dataset)
    assert res == True


# =============================================================================
# TEST get_remote_datasets_paged — uses metadata_parser transformation
# =============================================================================

@patch_both
def test_get_remote_datasets_paged_ok(mock_oai, mock_get):
    """
    One page of 3 open Dataset records, empty resumption token.
    Citation/license is fetched from the Dataverse export API and merged into
    each record before parsing. Verifies the enriched output matches
    OAIHarvester_mapping_ckan_dict for the first record.
    """
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_ok'
    obj.osnadata_export_to_dataverse_json_url = 'test_osnadata_export_dataset'
    obj.current_schema_report = {'status_ok': True}  # skip HTTP call in _get_schema_report

    result = obj.get_remote_datasets_paged()

    assert result["resumptionToken"] is None
    assert len(result["ds_list"]) == 3
    assert result["ds_list"][0] == OAIHarvester_mapping_ckan_dict


@patch.object(OAIHarvester, '_request', autospec=True, side_effect=mocked_oai_request)
def test_get_remote_datasets_paged_oai_error(mock_request):
    """OAI <error> response returns empty ds_list."""
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_error'

    result = obj.get_remote_datasets_paged()

    assert result == {"ds_list": [], "resumptionToken": None}


@patch.object(OAIHarvester, '_request', autospec=True, side_effect=mocked_oai_request)
def test_get_remote_datasets_paged_no_response(mock_request):
    """Network failure returns empty ds_list."""
    obj = OSNADATA_ParserProfile()
    obj.harvester.base_url = 'test_osnadata_get_datasets_list_no_response'

    result = obj.get_remote_datasets_paged()

    assert result == {"ds_list": [], "resumptionToken": None}


# =============================================================================
# TEST metadata_parser mappings
# =============================================================================

def test_parse_osnaData_OAI_RECORD_to_LDM_CKAN_DICT_using_mapping():
    """
    Verifies that parse_osnaData_OAI_RECORD_to_LDM_CKAN_DICT_using_mapping
    correctly converts an enriched OAI record (with citation/license from the
    Dataverse export API already attached) to the LDM CKAN schema.
    """
    parser = OSNADATA_ParserProfile()
    res_dict = parser.parse_osnaData_OAI_RECORD_to_LDM_CKAN_DICT_using_mapping(OAIHarvester_parsed_record_enriched)
    print("\n\nMAPPING RESULT:\n", res_dict)
    # assert res_dict == OAIHarvester_mapping_ckan_dict
    assert res_dict == osnadata_dataset_parsed_to_ckan_dict

