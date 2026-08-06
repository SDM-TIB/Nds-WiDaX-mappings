# pytest --ckan-ini=test.ini ckanext/tibimport/tests/test_LEUPHANA_profile.py -s

from ckanext.tibimport.LEUPHANA_ParserProfile import LEUPHANA_ParserProfile
from ckanext.tibimport.logic2 import LDM_DatasetImport
from ckanext.tibimport.dataCite_API_Search import dataCite_API_Search
from ckanext.tibimport.oai_harvester import OAIHarvester, OAIError, OAIHarvestError
import pytest

from LEUPHANA_Profile_Mocks import local_organization_data, local_dataset_data, expected_list_of_dict_from_leuphana, \
                                   resumption_token_ok, resumption_token_empty, leuphana_dataset_parsed_to_dict,  \
                                   leuphana_dataset_parsed_to_ckan_dict, leuphana_all_datasets_list_response, LDM_imported_dataset, \
                                   OAIHarvester_mapping_ckan_dict
# from OSNADATA_Profile_Mocks import , , , \
#     , , \
#     ckan_dict_of_imported_dataset, leuphana_dict_of_imported_dataset, local_organization_data, local_dataset_data

import sqlalchemy as sa


import xmltodict
from xml.etree import ElementTree

import pprint

import requests
from requests.models import Response
import json
from unittest.mock import Mock, patch
from ckan.plugins import toolkit

skip_test_searching_leuphana_website = True
skip_test_retrieve_all_datasets_from_source = False
skip_test_DatasetImport = True
# #@pytest.mark.skipif(skip_test1, reason="slows all the work") # put before conditional test to skip

# #logged_user = "test.ckan.net"


# TEST LDM_DatasetImport
# **********************

# SEARCH ORGANIZATION - NOT FOUND
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_org_not_found():
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    org_name = "TEST-3499-944kdf20"
    res_dict = obj.get_local_organization(org_name)
    assert res_dict == {}

# INSERT ORGANIZATION
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_insert_local_org():
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    org_dict = local_organization_data
    obj.insert_organization(org_dict)

    res_dict = obj.get_local_organization(org_dict['name'])
    print(res_dict)
    assert res_dict['name'] == org_dict['name']

# SEARCH ORGANIZATION - FOUND
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_org_found():
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    org_dict = local_organization_data
    res_dict = obj.get_local_organization(org_dict['name'])
    assert res_dict['name'] == org_dict['name']

# SEARCH DATASET NOT FOUND
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_dataset_not_found():
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    ds_name = "TEST-3499-944kdf20"
    res_dict = obj.get_local_dataset(ds_name)
    assert res_dict == {}


# INSERT DATASET
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_insert_local_dataset():

    # Delete test dataset from DB
    db_url = toolkit.config['sqlalchemy.url']
    engine = sa.create_engine(db_url)
    result = engine.execute(u"DELETE FROM package_extra WHERE package_id = '476cdf71-1048-4a6f-a28a-58fff547dae5'")
    result = engine.execute(u"DELETE FROM package WHERE id = '476cdf71-1048-4a6f-a28a-58fff547dae5'")

    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    ds_dict = local_dataset_data
    obj.insert_dataset(ds_dict)

    res_dict = obj.get_local_dataset(ds_dict['name'])
    ds_dict['metadata_created'] = res_dict['metadata_created']
    ds_dict['metadata_modified'] = res_dict['metadata_modified']

    print(res_dict)
    assert res_dict['name'] == ds_dict['name']

# SEARCH DATASET FOUND
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_search_local_dataset_found():
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    ds_dict = local_dataset_data

    ds_name = local_dataset_data['name']
    res_dict = obj.get_local_dataset(ds_name)
    ds_dict['metadata_created'] = res_dict['metadata_created']
    ds_dict['metadata_modified'] = res_dict['metadata_modified']
    ds_dict['state'] = res_dict['state']
    assert res_dict['name'] == ds_dict['name']

# UPDATE DATASET
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_update_local_dataset():

    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    ds_dict = local_dataset_data

    ds_name = local_dataset_data['name']
    ds_dict['author'] = "New Test Author"
    obj.update_dataset(ds_dict)

    res_dict = obj.get_local_dataset(ds_name)
    assert res_dict['author'] == ds_dict['author']


# DELETE DATASET
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_delete_local_dataset():
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    ds_dict = local_dataset_data
    obj.delete_dataset(ds_dict['name'])

    res_dict = obj.get_local_dataset(ds_dict['name'])

    assert res_dict['state'] == 'deleted'

# DELETE ORGANIZATION
@pytest.mark.skipif(skip_test_DatasetImport, reason="slows all the work")
def test_delete_local_org(app):
    parser = LEUPHANA_ParserProfile()
    obj = LDM_DatasetImport(parser)

    org_dict = local_organization_data
    obj.delete_organization(org_dict['name'])

    res_dict = obj.get_local_organization(org_dict['name'])

    assert res_dict == {}


# TEST leuphana API
# *****************

# Test remote connections
# ***********************
#https://pubdata.leuphana.de/oai/request?verb=ListIdentifiers&metadataPrefix=oai_dc&set=com_123456789_3
def test_leuphana_harvesting_remote_ok():
    parser = LEUPHANA_ParserProfile()
    # https://leuphana.ub.uni-osnabrueck.de/sever/oai?verb=ListRecords&metadataPrefix=dim
    url = parser.leuphana_ListRecords_url_first_time
       
    response = requests.get(url)

    print('XML fist page response:', response.content[-1000:], url)
    assert response.ok



# =============================================================================
# MOCK LAYER 1 — OAIHarvester._request  (OAI-PMH calls)
# =============================================================================

def mocked_oai_request(self_harvester, params):
    base = self_harvester.base_url

    if base == 'test_leuphana_get_datasets_list_error':
        with open('./ckanext/tibimport/tests/leuphana_error.xml', 'r') as f:
            tree = ElementTree.fromstring(f.read())
        self_harvester._check_oai_error(tree)
        return tree  # never reached

    elif base in ('test_leuphana_get_datasets_list_ok', 'test_get_all_datasets_dicts'):
        with open('./ckanext/tibimport/tests/leuphana_dataset_dim.xml', 'r') as f:
            xml_data = f.read()
        xml_data = xml_data.replace(
            '<resumptionToken completeListSize="129" cursor="0">dim///com_123456789_3/100</resumptionToken>',
            '<resumptionToken />')
        return ElementTree.fromstring(xml_data)

    elif base == 'test_leuphana_get_datasets_list_no_response':
        raise OAIHarvestError("HTTP Error 404: Not Found")

    else:
        raise OAIHarvestError(f"mocked_oai_request: no fixture for base_url={base!r}")


# =============================================================================
# MOCK LAYER 2 — requests.get  (DataCite API calls only)
# =============================================================================

def mocked_datacite_get(*args, **kwargs):
    request_url = args[0]
    response = Response()
    response.status_code = 200
    if 'https://api.datacite.org/dois/10.48548/' in request_url:
        file_name = 'leuphana_' + request_url.split('/')[-1] + '.json'
        with open('./ckanext/tibimport/tests/' + file_name, 'r') as f:
            response._content = f.read().encode()
    return response


# Convenience decorator: stacks both patches for tests that need DataCite enrichment.
# Argument order in decorated function: def test_xxx(mock_oai, mock_get): ...
def patch_both(func):
    func = patch(
        'ckanext.tibimport.LEUPHANA_ParserProfile.requests.get',
        side_effect=mocked_datacite_get
    )(func)
    func = patch.object(
        OAIHarvester, '_request',
        autospec=True,
        side_effect=mocked_oai_request
    )(func)
    return func


# TEST leuphana get_dataset_list
@patch.object(OAIHarvester, '_request', autospec=True, side_effect=mocked_oai_request)
def test_leuphana_get_datasets_list_error(mock_request):
    obj = LEUPHANA_ParserProfile()
    obj.harvester.base_url = 'test_leuphana_get_datasets_list_error'
    res_dict = obj.get_datasets_list()
    assert res_dict == []


@patch_both
def test_leuphana_get_datasets_list_ok(mock_oai, mock_get):
    obj = LEUPHANA_ParserProfile()
    obj.harvester.base_url = 'test_leuphana_get_datasets_list_ok'
    res_dict = obj.get_datasets_list()

    assert 5 == len(res_dict)
    print('DATASETS LIST: ', res_dict)
    assert res_dict == expected_list_of_dict_from_leuphana


@patch_both
def test_leuphana_get_datasets_list_filter_not_open(mock_oai, mock_get):
    obj = LEUPHANA_ParserProfile()
    obj.harvester.base_url = 'test_leuphana_get_datasets_list_ok'
    res_dict = obj.get_datasets_list()

    res_dict[0]['doi_metadata']['rightsList'] = []
    res_dict[1]['doi_metadata']['rightsList'] = []
    res_dict = obj.filter_open_datasets(res_dict)
    assert 3 == len(res_dict)


@patch_both
def test_leuphana_get_datasets_list_filter_not_json_found(mock_oai, mock_get):
    obj = LEUPHANA_ParserProfile()
    obj.harvester.base_url = 'test_leuphana_get_datasets_list_ok'
    res_dict = obj.get_datasets_list()

    res_dict[0]['doi_metadata'] = {}
    res_dict = obj.filter_open_datasets(res_dict)
    assert 4 == len(res_dict)


@patch.object(OAIHarvester, '_request', autospec=True, side_effect=mocked_oai_request)
def test_leuphana_get_datasets_list_no_response(mock_request):
    obj = LEUPHANA_ParserProfile()
    obj.harvester.base_url = 'test_leuphana_get_datasets_list_no_response'
    res_dict = obj.get_datasets_list()
    print("RES DICT:\n", res_dict)
    assert res_dict == []

    
def test_get_leuphana_resumption_token_ok():
    obj = LEUPHANA_ParserProfile()

    # Emule harvester response from file
    with open('./ckanext/tibimport/tests/leuphana_dataset_dim.xml', 'r') as file:
        xml_data = file.read()
    xml_tree_data = ElementTree.fromstring(xml_data)

    resumption_token = obj._get_leuphana_resumption_token(xml_tree_data)

    print("RESUMPTION TOKEN: ", resumption_token)

    #tags = [elem.tag for elem in xml_tree_data.iter()]
    #print(tags)

    assert resumption_token == resumption_token_ok

def test_get_leuphana_resumption_token_empty():
    obj = LEUPHANA_ParserProfile()

    # Emule harvester response from file
    with open('./ckanext/tibimport/tests/leuphana_dataset_dim.xml', 'r') as file:
        xml_data = file.read()
    # with empty token
    xml_data = xml_data.replace(
             '<resumptionToken completeListSize="129" cursor="0">dim///com_123456789_3/100</resumptionToken>', '<resumptionToken />')
        
    xml_tree_data = ElementTree.fromstring(xml_data)

    resumption_token = obj._get_leuphana_resumption_token(xml_tree_data)
 #   print("RESUMPTION TOKEN EMPTY: ", resumption_token)

    assert resumption_token == resumption_token_empty

def test_get_leuphana_resumption_token_not_present():
    obj = LEUPHANA_ParserProfile()

    # Emule harvester response from file
    with open('./ckanext/tibimport/tests/leuphana_dataset_dim.xml', 'r') as file:
        xml_data = file.read()
    # with no token data
    xml_data = xml_data.replace(
             '<resumptionToken completeListSize="129" cursor="0">dim///com_123456789_3/100</resumptionToken>', '')
    
    xml_tree_data = ElementTree.fromstring(xml_data)

    resumption_token = obj._get_leuphana_resumption_token(xml_tree_data)
   
    assert resumption_token == resumption_token_empty

def test_parse_leuphana_RECORD_DICT_to_LDM_CKAN_DICT():
    leuphana_dict = leuphana_dataset_parsed_to_dict

    parser = LEUPHANA_ParserProfile()

    res_dict = parser.parse_leuphana_RECORD_DICT_to_LDM_CKAN_DICT(leuphana_dict)
    

    print("\n\nPARSED DICT: \n", res_dict)

    assert res_dict == leuphana_dataset_parsed_to_ckan_dict


@patch_both
def test_get_all_datasets_dicts(mock_oai, mock_get):
    obj = LEUPHANA_ParserProfile()
    obj.harvester.base_url = 'test_get_all_datasets_dicts'
    res_list = obj.get_all_datasets_dicts()
    print("\nALL Datasets List:\n", res_list)
    print("\nLEN:", len(res_list))
    assert res_list == leuphana_all_datasets_list_response


@pytest.mark.skipif(skip_test_retrieve_all_datasets_from_source, reason="slows all the work")
def test_all_datasets_are_retrieved():
    obj = LEUPHANA_ParserProfile()

    res_list = obj.get_all_datasets_dicts()
    
    print("\nALL Datasets List:\n", res_list)
    print("\nTOTAL LEUPHANA DS:", len(res_list))
    # for ds in res_list:
    #     title = ds['metadata']['leuphanaDataset']['titles'][0]['title']['title']
    #     if title in ('Open Educational Practices an niedersächsischen Hochschulen - Auflistung der im Datensatz enthaltenen Quellen', 'Digital Health Platform Governance Document Register'):
    #         print("\n\nDS CONFLICT:", ds['header']['json_ld'])
    assert len(res_list) == obj.total_leuphana_datasets

def test_should_be_updated_false():
    obj = LEUPHANA_ParserProfile()

    local_dataset = LDM_imported_dataset
    remote_dataset = leuphana_dataset_parsed_to_ckan_dict

    res = obj.should_be_updated(local_dataset, remote_dataset)

    assert res == False

def test_should_be_updated_true():
    obj = LEUPHANA_ParserProfile()

    local_dataset = LDM_imported_dataset
    remote_dataset = leuphana_dataset_parsed_to_ckan_dict
    remote_dataset['title'] = 'New Title'
    print("\n\nCKAN DATASET:\n", local_dataset)
    print("\n\nLEO IMPORTED DATASET:\n", remote_dataset)
    res = obj.should_be_updated(local_dataset, remote_dataset)

    assert res == True

def test_check_current_schema():
    obj = LEUPHANA_ParserProfile()
    res = obj.check_current_schema()

    print("\nSCHEMA REPORT: ", res)

    assert res['status_ok']

# # def test_process_all_remote_datasets():

# #     obj = LEUPHANA_ParserProfile()
# #     # process all remote datasets from OSNADATA
# #     obj.get_all_datasets_dicts()

# #     # Expecting errors in code in case something goes wrong during parsing
# #     assert True


# =============================================================================
# TEST metadata_parser mappings
# =============================================================================

def test_parse_leuphana_RECORD_DICT_to_LDM_CKAN_DICT_using_mapping():
    """
    Verifies that parse_leuphana_RECORD_DICT_to_LDM_CKAN_DICT_using_mapping
    correctly converts the combined OAI+DataCite dict to the LDM CKAN schema
    using LEU_profile_mapping.json and the registered custom functions.

    Key differences from leuphana_dataset_parsed_to_ckan_dict (old path):
    - authors: unified list instead of split author/givenName/familyName/orcid/extra_authors
    - ORCIDs extracted from doi_metadata.creators.nameIdentifiers (full URI stripped)
    - publication_year uses DataCite.PublicationYear ('2024') rather than the
      datestamp fallback ('2025') that the old code produced due to a key-casing mismatch
    """
    parser = LEUPHANA_ParserProfile()
    res_dict = parser.parse_leuphana_RECORD_DICT_to_LDM_CKAN_DICT_using_mapping(leuphana_dataset_parsed_to_dict)
    print("\n\nMAPPING RESULT:\n", res_dict)
    assert res_dict == OAIHarvester_mapping_ckan_dict
