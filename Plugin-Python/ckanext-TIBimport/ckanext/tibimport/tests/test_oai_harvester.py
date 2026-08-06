"""
Unit Tests for OAIHarvester Class

This module contains unit tests for the OAIHarvester class to ensure
all functionality works as expected.
"""
# pytest --ckan-ini=test.ini ckanext/tibimport/tests/test_oai_harvester.py -s

import unittest
import json
from ckanext.tibimport.oai_harvester import OAIHarvester, OAIError, OAIHarvestError
import logging

# ---------------------------------------------------------------------------
# Smoke-test — run:  python oai_harvester.py
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REPOS = [
    {
        "label":  "osnaData (oai_datacite)",
        "url":    "https://osnadata.ub.uni-osnabrueck.de/oai",
        "prefix": "oai_datacite",
    },
    # Uncomment to test other repos:
    # {
    #     "label":  "Zenodo (oai_datacite)",
    #     "url":    "https://zenodo.org/oai2d",
    #     "prefix": "oai_datacite",
    # },
    # {
    #     "label":  "arXiv (oai_dc)",
    #     "url":    "https://export.arxiv.org/oai2",
    #     "prefix": "oai_dc",
    # },
]

def test_OAIHarvester():
    """Test cases for OAIHarvester class."""

    for repo in REPOS:
        print(f"\n{'='*60}")
        print(f"Repository : {repo['label']}")
        print(f"URL        : {repo['url']}")
        print("="*60)

        h = OAIHarvester(repo["url"], metadata_prefix=repo["prefix"])
        print(repr(h))

        # Harvest first 3 records only
        count = 0
        for record in h.harvest_all():
            count += 1
            hdr = record["header"]
            print(f"\n--- Record {count}: {hdr['identifier']} ---")
            print(json.dumps(record, indent=2, ensure_ascii=False))
            if count >= 3:
                break

        print(f"\nShowed {count} record(s) from {repo['label']}.")

        assert count == 3
       

# ---------------------------------------------------------------------------
# Smoke-test  —  python oai_harvester.py
# ---------------------------------------------------------------------------


#     # ── Two synthetic "pages" — 3 records total (1 restricted = filtered out)
#     PAGE_1 = b"""<?xml version="1.0" encoding="UTF-8"?>
# <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
#   <request verb="ListRecords">https://osnadata.ub.uni-osnabrueck.de/oai</request>
#   <ListRecords>
#     <record>
#       <header><identifier>doi:10.26249/FK2/AAA</identifier><datestamp>2025-01-01T00:00:00Z</datestamp></header>
#       <metadata><resource xmlns="http://datacite.org/schema/kernel-4">
#         <identifier identifierType="DOI">10.26249/FK2/AAA</identifier>
#         <creators><creator><creatorName nameType="Personal">Alpha, A.</creatorName></creator></creators>
#         <titles><title>Open Dataset Alpha</title></titles>
#         <publisher>osnaData</publisher><publicationYear>2025</publicationYear>
#         <resourceType resourceTypeGeneral="Dataset"/>
#         <rightsList><rights rightsURI="info:eu-repo/semantics/openAccess"/></rightsList>
#         <descriptions><description descriptionType="Abstract">Alpha abstract.</description></descriptions>
#         <geoLocations/>
#       </resource></metadata>
#     </record>
#     <record>
#       <header><identifier>doi:10.26249/FK2/BBB</identifier><datestamp>2025-02-01T00:00:00Z</datestamp></header>
#       <metadata><resource xmlns="http://datacite.org/schema/kernel-4">
#         <identifier identifierType="DOI">10.26249/FK2/BBB</identifier>
#         <creators><creator><creatorName nameType="Personal">Beta, B.</creatorName></creator></creators>
#         <titles><title>Restricted Dataset Beta</title></titles>
#         <publisher>osnaData</publisher><publicationYear>2025</publicationYear>
#         <resourceType resourceTypeGeneral="Dataset"/>
#         <rightsList><rights rightsURI="info:eu-repo/semantics/restrictedAccess"/></rightsList>
#         <descriptions><description descriptionType="Abstract">Beta abstract.</description></descriptions>
#         <geoLocations/>
#       </resource></metadata>
#     </record>
#     <resumptionToken cursor="0" completeListSize="3">tok-page2</resumptionToken>
#   </ListRecords>
# </OAI-PMH>"""

#     PAGE_2 = b"""<?xml version="1.0" encoding="UTF-8"?>
# <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
#   <ListRecords>
#     <record>
#       <header><identifier>doi:10.26249/FK2/CCC</identifier><datestamp>2025-03-01T00:00:00Z</datestamp></header>
#       <metadata><resource xmlns="http://datacite.org/schema/kernel-4">
#         <identifier identifierType="DOI">10.26249/FK2/CCC</identifier>
#         <creators><creator>
#           <creatorName nameType="Personal">Gamma, G.</creatorName>
#           <nameIdentifier nameIdentifierScheme="ORCID">0000-0002-1234-5678</nameIdentifier>
#         </creator></creators>
#         <titles><title>Open Dataset Gamma</title></titles>
#         <publisher>osnaData</publisher><publicationYear>2025</publicationYear>
#         <resourceType resourceTypeGeneral="Dataset"/>
#         <rightsList><rights rightsURI="info:eu-repo/semantics/openAccess"/></rightsList>
#         <descriptions><description descriptionType="Abstract">Gamma abstract.</description></descriptions>
#         <geoLocations/>
#       </resource></metadata>
#     </record>
#     <resumptionToken cursor="2" completeListSize="3"/>
#   </ListRecords>
# </OAI-PMH>"""

#     PAGES = {"": PAGE_1, "tok-page2": PAGE_2}

#     # Monkey-patch _request to serve synthetic pages (no real HTTP)
#     def _fake_request(self, params):
#         tok  = params.get("resumptionToken", "")
#         tree = ET.fromstring(PAGES[tok])
#         self._check_oai_error(tree)
#         return tree

#     OAIHarvester._request = _fake_request

#     # ── Pipeline hooks ────────────────────────────────────────────────
#     def is_open(record: dict) -> bool:
#         """Keep only openAccess records."""
#         rights = record.get("metadata", {}).get("rightsList", [])
#         return any("openAccess" in (r.get("rightsURI") or "") for r in rights)

#     def add_citation(record: dict) -> dict:
#         """Simulate enrichment from a secondary API."""
#         record.setdefault("metadata", {})["citation"] = (
#             f"Cite as: {record['header']['identifier']} (2025)"
#         )
#         return record

#     def to_ckan(record: dict) -> dict:
#         """Simulate transformation to CKAN schema."""
#         m      = record.get("metadata", {})
#         titles = m.get("titles", [])
#         return {
#             "id":       record["header"]["identifier"],
#             "title":    titles[0]["value"] if titles else None,
#             "citation": m.get("citation"),
#             "year":     m.get("publicationYear"),
#         }

#     def get_schema(tree: ET.Element) -> dict:
#         req_el = tree.find("{http://www.openarchives.org/OAI/2.0/}request")
#         return {"endpoint": req_el.text.strip() if req_el is not None and req_el.text else None}

#     # ── Build harvester ───────────────────────────────────────────────
#     h = OAIHarvester(
#         "https://osnadata.ub.uni-osnabrueck.de/oai",
#         filter_fn    = is_open,
#         enrich_fn    = add_citation,
#         transform_fn = to_ckan,
#         schema_fn    = get_schema,
#     )
#     print(repr(h), "\n")

#     SEP = "=" * 60

#     # ── Option B: harvest_page() — mirrors get_remote_datasets_paged ──
#     print(SEP)
#     print("Option B — harvest_page()  (exact drop-in for get_remote_datasets_paged)")
#     print(SEP)

#     class MockCaller:
#         """Simulates your existing class that owns the harvester."""
#         total_osnaData_datasets = 0
#         current_schema_report   = None

#         def __init__(self):
#             self.harvester = OAIHarvester(
#                 "https://osnadata.ub.uni-osnabrueck.de/oai",
#                 filter_fn    = is_open,
#                 enrich_fn    = add_citation,
#                 transform_fn = to_ckan,
#                 schema_fn    = get_schema,
#             )

#         def get_remote_datasets_paged(self, resumption_token: str = ""):
#             # ← this is the entire replacement for your old method
#             page = self.harvester.harvest_page(resumption_token or None)
#             self.total_osnaData_datasets += page["pageRecordCount"]
#             if page["schemaReport"]:               # only truthy on page 1
#                 self.current_schema_report = page["schemaReport"]
#             return {
#                 "ds_list":         page["records"],
#                 "resumptionToken": page["resumptionToken"],
#             }

#     caller = MockCaller()

#     # Call 1 — first page (no token, as your caller does with '')
#     result = caller.get_remote_datasets_paged("")
#     print(f"\nCall 1  token_in=''")
#     print(f"  schema_report  : {caller.current_schema_report}")
#     print(f"  ds_list        : {result['ds_list']}")
#     print(f"  resumptionToken: {result['resumptionToken']!r}")
#     print(f"  total_datasets : {caller.total_osnaData_datasets}")

#     # Call 2 — second page (pass the token you got back)
#     result = caller.get_remote_datasets_paged(result["resumptionToken"])
#     print(f"\nCall 2  token_in='tok-page2'")
#     print(f"  schema_report  : {caller.current_schema_report}  (unchanged — only set on page 1)")
#     print(f"  ds_list        : {result['ds_list']}")
#     print(f"  resumptionToken: {result['resumptionToken']!r}  (None = last page)")
#     print(f"  total_datasets : {caller.total_osnaData_datasets}")

#     # ── Option C: harvest_pages() generator ───────────────────────────
#     print(f"\n{SEP}")
#     print("Option C — harvest_pages()  (class drives the token loop)")
#     print(SEP)
#     h.reset()
#     for page in h.harvest_pages():
#         print(f"  Page {page['pageNumber']}  "
#               f"kept={page['pageRecordCount']}  "
#               f"cursor={page['cursor']}  "
#               f"total={page['completeListSize']}  "
#               f"last={page['isLastPage']}")
#         for rec in page["records"]:
#             print(f"    ✓ {rec['id']}  — {rec['title']}")

#     # ── Option A: harvest_all() ───────────────────────────────────────
#     print(f"\n{SEP}")
#     print("Option A — harvest_all()  (streaming, one record at a time)")
#     print(SEP)
#     h.reset()
#     for rec in h.harvest_all():
#         print(f"  ▸ {rec['id']}  —  {rec['title']}  —  {rec['citation']}")

#     print(f"\nFinal: {repr(h)}")