"""
oai_harvester.py
================
A reusable, repository-agnostic OAI-PMH 2.0 harvester with a built-in
DataCite kernel-4 / Dublin Core parser and a three-stage per-record pipeline.

Supported repositories: any OAI-PMH 2.0 endpoint (osnaData, Zenodo, arXiv …).

────────────────────────────────────────────────────────────────────────────
HARVESTING STRATEGIES — choose one per use-case
────────────────────────────────────────────────────────────────────────────

Option A — harvest_all()  »  streaming, record by record
─────────────────────────────────────────────────────────
The class drives the whole loop.  Memory footprint = one page at a time.

    harvester = OAIHarvester("https://osnadata.ub.uni-osnabrueck.de/oai")
    for record in harvester.harvest_all():
        print(record["header"]["identifier"])


Option B — harvest_page()  »  YOU control the loop          ← new
──────────────────────────────────────────────────────────────────────────
Drop-in replacement for a hand-rolled get_remote_datasets_paged() method.
Fetches exactly ONE page, applies the filter / enrich / transform pipeline,
and returns a plain dict you can pass straight back to your caller:

    {
        "records":          [...]       # filtered + enriched + transformed
        "resumptionToken":  str | None  # None on the last page
        "cursor":           int | None  # position in the full list
        "completeListSize": int | None  # total records in repo
        "isLastPage":       bool
        "pageNumber":       int         # 1-based, reset by reset()
        "pageRecordCount":  int         # records on this page after filter
        "schemaReport":     dict | None # populated on the FIRST page only
    }

    Typical replacement of get_remote_datasets_paged(resumption_token=''):

        # Build harvester once (e.g. in __init__ of your caller class)
        self.harvester = OAIHarvester(
            base_url        = self.osnaData_base_url,
            filter_fn       = self.filter_open_datasets,       # record→bool
            enrich_fn       = self._add_citation_and_license,  # record→record
            transform_fn    = self.parse_record_to_ckan_dict,  # record→dict
            schema_fn       = self._get_schema_report,         # tree→dict
        )

        # Then replace get_remote_datasets_paged with:
        def get_remote_datasets_paged(self, resumption_token=''):
            self.set_log("infos_searching_ds")
            page = self.harvester.harvest_page(resumption_token or None)
            self.total_osnaData_datasets += page["pageRecordCount"]
            if page["schemaReport"]:                   # only truthy on page 1
                self.current_schema_report = page["schemaReport"]
            return {
                "ds_list":        page["records"],
                "resumptionToken": page["resumptionToken"],
            }


Option C — harvest_pages()  »  generator of page dicts       ← new
──────────────────────────────────────────────────────────────────────────
Like harvest_page() but the class drives the token-passing loop.
Use it when you only need logic once per page (e.g. bulk DB inserts).

    for page in harvester.harvest_pages():
        save_to_db(page["records"])
        print(f"Page {page['pageNumber']}: {page['completeListSize']} total")


Pipeline hooks (all optional, passed to the constructor)
────────────────────────────────────────────────────────
    filter_fn(record: dict) -> bool
        Called after parsing.  Return False to drop the record.

    enrich_fn(record: dict) -> dict
        Called after filtering.  Return the (possibly modified) record.
        Typical use: fetch citation / license data from a secondary API.

    transform_fn(record: dict) -> Any
        Called after enrichment.  Return value replaces the record in the
        output list (e.g. convert to your internal CKAN schema dict).

    schema_fn(xml_tree: ET.Element) -> dict
        Called on the raw XML tree of the FIRST page only.  Use it to
        capture schema / header data that sits outside individual records
        (mirrors _get_schema_report in your original code).

Session state
─────────────
    harvester.total_harvested    # cumulative records kept after filter
    harvester.page_number        # current 1-based page counter
    harvester.schema_report      # schema data from the first page
    harvester.reset()            # restart counters without rebuilding

────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import html
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Generator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------
_NS_OAI    = "{http://www.openarchives.org/OAI/2.0/}"
_NS_DC4    = "{http://datacite.org/schema/kernel-4}"
_NS_OAI_DC = "{http://www.openarchives.org/OAI/2.0/oai_dc/}"
_NS_DC     = "{http://purl.org/dc/elements/1.1/}"
_NS_XML    = "{http://www.w3.org/XML/1998/namespace}"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class OAIError(Exception):
    """Raised when the repository returns an OAI-PMH error element."""
    def __init__(self, code: str, message: str) -> None:
        self.code    = code
        self.message = message
        super().__init__(f"OAI-PMH error [{code}]: {message}")


class OAIHarvestError(IOError):
    """Raised on unrecoverable HTTP or network failures."""


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class OAIHarvester:
    """
    Repository-agnostic OAI-PMH 2.0 client with a built-in DataCite parser
    and an optional filter / enrich / transform pipeline.

    Parameters
    ----------
    base_url : str
        OAI-PMH endpoint, e.g. "https://osnadata.ub.uni-osnabrueck.de/oai".
    metadata_prefix : str
        "oai_datacite" (default) or "oai_dc".
    filter_fn : callable(record) -> bool, optional
        Keep the record when this returns True; drop it on False.
    enrich_fn : callable(record) -> dict, optional
        Augment a record (e.g. fetch citation data) and return it.
        Only called on records that passed the filter.
    transform_fn : callable(record) -> Any, optional
        Convert a record to your internal schema.  The return value is what
        ends up in page["records"] and is yielded by harvest_all().
        Only called after enrichment.
    schema_fn : callable(ET.Element) -> dict, optional
        Called on the raw XML tree of the FIRST page only.  Use it to
        capture schema/header data that sits outside individual records.
    timeout : int
        HTTP timeout in seconds (default 60).
    max_retries : int
        Retry attempts on transient network errors (default 3).
    retry_delay : float
        Initial retry wait in seconds, doubled each attempt (default 5).
    request_delay : float
        Polite pause between successive page requests (default 1).
    """

    def __init__(
        self,
        base_url:        str,
        metadata_prefix: str = "oai_datacite",
        *,
        filter_fn:    Callable[[dict], bool]        | None = None,
        enrich_fn:    Callable[[dict], dict]        | None = None,
        transform_fn: Callable[[dict], Any]         | None = None,
        schema_fn:    Callable[[ET.Element], dict]  | None = None,
        timeout:      int   = 60,
        max_retries:  int   = 3,
        retry_delay:  float = 5.0,
        request_delay: float = 1.0,
    ) -> None:
        # ── Configuration (immutable after construction) ──────────────
        self.base_url        = base_url.rstrip("/")
        self.metadata_prefix = metadata_prefix
        self.filter_fn       = filter_fn
        self.enrich_fn       = enrich_fn
        self.transform_fn    = transform_fn
        self.schema_fn       = schema_fn
        self.timeout         = timeout
        self.max_retries     = max_retries
        self.retry_delay     = retry_delay
        self.request_delay   = request_delay

        # ── Session state (reset() re-initialises these) ──────────────
        self._total_harvested: int        = 0
        self._page_number:     int        = 0
        self._schema_report:   dict | None = None

    # ------------------------------------------------------------------
    # Session state — read-only properties
    # ------------------------------------------------------------------

    @property
    def total_harvested(self) -> int:
        """Cumulative count of records kept (after filter) across all pages."""
        return self._total_harvested

    @property
    def page_number(self) -> int:
        """Current 1-based page counter (0 before first page is fetched)."""
        return self._page_number

    @property
    def schema_report(self) -> dict | None:
        """Schema data captured from the first page via schema_fn, or None."""
        return self._schema_report

    def reset(self) -> None:
        """
        Reset session counters so the same instance can start a new harvest.

        Does NOT change configuration (base_url, prefix, pipeline hooks, …).

        Example
        -------
            harvester.reset()
            for page in harvester.harvest_pages(from_date="2025-01-01"):
                ...
        """
        self._total_harvested = 0
        self._page_number     = 0
        self._schema_report   = None
        logger.debug("OAIHarvester session reset.")

    # ------------------------------------------------------------------
    # Standard OAI-PMH verbs
    # ------------------------------------------------------------------

    def identify(self) -> dict[str, Any]:
        """Call Identify and return repository information."""
        tree  = self._request({"verb": "Identify"})
        id_el = self._find(tree, "Identify")
        if id_el is None:
            return {}

        def _t(tag: str) -> str | None:
            return self._text(self._find(id_el, tag))

        return {
            "repositoryName":    _t("repositoryName"),
            "baseURL":           _t("baseURL"),
            "protocolVersion":   _t("protocolVersion"),
            "adminEmails":       [self._text(e) for e in id_el.findall(f"{_NS_OAI}adminEmail")],
            "earliestDatestamp": _t("earliestDatestamp"),
            "deletedRecord":     _t("deletedRecord"),
            "granularity":       _t("granularity"),
            "compressions":      [self._text(e) for e in id_el.findall(f"{_NS_OAI}compression")],
            "descriptions":      [self._elem_to_dict(d) for d in id_el.findall(f"{_NS_OAI}description")],
        }

    def list_sets(self) -> list[dict[str, str | None]]:
        """Call ListSets and return all available sets (follows resumption)."""
        sets:   list[dict[str, str | None]] = []
        params: dict[str, str] = {"verb": "ListSets"}
        while True:
            tree  = self._request(params)
            ls_el = self._find(tree, "ListSets")
            if ls_el is None:
                break
            for s in ls_el.findall(f"{_NS_OAI}set"):
                sets.append({
                    "setSpec":        self._text(self._find(s, "setSpec")),
                    "setName":        self._text(self._find(s, "setName")),
                    "setDescription": self._text(self._find(s, "setDescription")),
                })
            token = self._resumption_token(ls_el)
            if not token:
                break
            params = {"verb": "ListSets", "resumptionToken": token}
            time.sleep(self.request_delay)
        return sets

    def list_metadata_formats(
        self, identifier: str | None = None
    ) -> list[dict[str, str | None]]:
        """Call ListMetadataFormats (optionally scoped to one identifier)."""
        params: dict[str, str] = {"verb": "ListMetadataFormats"}
        if identifier:
            params["identifier"] = identifier
        tree   = self._request(params)
        lmf_el = self._find(tree, "ListMetadataFormats")
        if lmf_el is None:
            return []
        return [
            {
                "metadataPrefix":    self._text(self._find(mf, "metadataPrefix")),
                "schema":            self._text(self._find(mf, "schema")),
                "metadataNamespace": self._text(self._find(mf, "metadataNamespace")),
            }
            for mf in lmf_el.findall(f"{_NS_OAI}metadataFormat")
        ]

    def list_identifiers(
        self,
        from_date:  str | None = None,
        until_date: str | None = None,
        set_spec:   str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Stream lightweight header dicts via ListIdentifiers (no metadata)."""
        params = self._base_params("ListIdentifiers", from_date, until_date, set_spec)
        while True:
            tree  = self._request(params)
            li_el = self._find(tree, "ListIdentifiers")
            if li_el is None:
                break
            for h in li_el.findall(f"{_NS_OAI}header"):
                yield self._parse_header(h)
            token = self._resumption_token(li_el)
            if not token:
                break
            params = {"verb": "ListIdentifiers", "resumptionToken": token}
            time.sleep(self.request_delay)

    def get_record(self, identifier: str) -> Any | None:
        """
        Fetch and parse a single record by its OAI identifier.

        The filter / enrich / transform pipeline is applied before returning.
        Returns None if the record is dropped by filter_fn.
        """
        params = {
            "verb":           "GetRecord",
            "identifier":     identifier,
            "metadataPrefix": self.metadata_prefix,
        }
        tree  = self._request(params)
        gr_el = self._find(tree, "GetRecord")
        if gr_el is None:
            raise OAIHarvestError(f"GetRecord returned no content for {identifier!r}")
        rec_el = gr_el.find(f"{_NS_OAI}record")
        if rec_el is None:
            raise OAIHarvestError(f"No <record> element found for {identifier!r}")
        return self._apply_pipeline(self.parse_record(rec_el))

    # ------------------------------------------------------------------
    # ── PAGED HARVESTING ───────────────────────────────────────────────
    # ------------------------------------------------------------------

    def harvest_page(
        self,
        resumption_token: str | None = None,
        *,
        from_date:  str | None = None,
        until_date: str | None = None,
        set_spec:   str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch exactly ONE page of records and return a structured result dict.

        This is the direct drop-in for a hand-rolled
        get_remote_datasets_paged(resumption_token) method.  You own the
        loop; the class handles HTTP, retries, XML parsing, and the full
        filter / enrich / transform pipeline.

        Parameters
        ----------
        resumption_token : str or None
            None (or omit) for the very first page.  Pass the token from the
            previous page's result for subsequent pages.  An empty string ""
            is treated as None so you can forward it without pre-processing.
        from_date, until_date : str, optional
            ISO 8601 date strings.  Ignored when resumption_token is set.
        set_spec : str, optional
            OAI set to restrict to.  Also ignored when a token is supplied.

        Returns
        -------
        dict with keys:
            records          — list after filter + enrich + transform
            resumptionToken  — str for next page, or None (last page)
            cursor           — int position in full list, or None
            completeListSize — total records in repo, or None
            isLastPage       — True when resumptionToken is None
            pageNumber       — 1-based, incremented each call
            pageRecordCount  — len(records) after filter
            schemaReport     — dict from schema_fn on page 1, else None

        Exact drop-in replacement example
        ----------------------------------
            # In __init__ of your caller class:
            self.harvester = OAIHarvester(
                base_url     = self.osnaData_base_url,
                filter_fn    = self.filter_open_datasets,
                enrich_fn    = self._add_citation_and_license,
                transform_fn = self.parse_record_to_ckan_dict,
                schema_fn    = self._get_schema_report,
            )

            # Replace get_remote_datasets_paged with:
            def get_remote_datasets_paged(self, resumption_token=''):
                self.set_log("infos_searching_ds")
                page = self.harvester.harvest_page(resumption_token or None)
                self.total_osnaData_datasets += page["pageRecordCount"]
                if page["schemaReport"]:       # only truthy on page 1
                    self.current_schema_report = page["schemaReport"]
                return {
                    "ds_list":         page["records"],
                    "resumptionToken": page["resumptionToken"],
                }
        """
        # Normalise empty string → None (caller convention compatibility)
        token = resumption_token if resumption_token else None

        # Build request parameters
        params: dict[str, str] = (
            {"verb": "ListRecords", "resumptionToken": token}
            if token
            else self._base_params("ListRecords", from_date, until_date, set_spec)
        )

        # Fetch the page
        tree = self._request(params)

        lr_el = self._find(tree, "ListRecords")
        if lr_el is None:
            logger.warning("No <ListRecords> element in response — returning empty page.")
            return self._empty_page()

        # Increment page counter
        self._page_number += 1

        # ── schema_fn: run once on the first page only ────────────────
        schema_report: dict | None = None
        if self._page_number == 1 and self.schema_fn is not None:
            try:
                schema_report       = self.schema_fn(tree)
                self._schema_report = schema_report
                logger.debug("Schema report captured on page 1.")
            except Exception as exc:
                logger.warning("schema_fn raised %s — ignoring.", exc)

        # ── Parse every <record> and run the pipeline ─────────────────
        records: list[Any] = []
        for rec_el in lr_el.findall(f"{_NS_OAI}record"):
            result = self._apply_pipeline(self.parse_record(rec_el))
            if result is not None:
                records.append(result)

        self._total_harvested += len(records)

        # ── Extract resumption token + pagination metadata ────────────
        tok_el             = lr_el.find(f"{_NS_OAI}resumptionToken")
        next_token: str | None = None
        cursor:     int | None = None
        complete:   int | None = None

        if tok_el is not None:
            raw = (tok_el.text or "").strip()
            next_token = raw if raw else None
            try:
                cursor   = int(tok_el.get("cursor", ""))
            except (ValueError, TypeError):
                cursor   = None
            try:
                complete = int(tok_el.get("completeListSize", ""))
            except (ValueError, TypeError):
                complete = None

        is_last = next_token is None

        logger.info(
            "Page %d: %d record(s) kept  cursor=%s  total=%s  last=%s",
            self._page_number, len(records), cursor, complete, is_last,
        )

        return {
            "records":          records,
            "resumptionToken":  next_token,
            "cursor":           cursor,
            "completeListSize": complete,
            "isLastPage":       is_last,
            "pageNumber":       self._page_number,
            "pageRecordCount":  len(records),
            "schemaReport":     schema_report,   # non-None on page 1 only
        }

    def harvest_pages(
        self,
        *,
        from_date:  str | None = None,
        until_date: str | None = None,
        set_spec:   str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Generator that yields one harvest_page result dict per page,
        driving the resumption-token loop automatically.

        Use this when you need per-page logic (bulk DB inserts, progress
        bars, etc.) rather than per-record logic.

        Yields
        ------
        dict — same structure as harvest_page() return value.

        Example
        -------
            for page in harvester.harvest_pages(from_date="2025-01-01"):
                save_to_database(page["records"])
                print(f"Page {page['pageNumber']} — "
                      f"total so far: {harvester.total_harvested}")
        """
        token: str | None = None
        while True:
            page = self.harvest_page(
                token,
                from_date=from_date,
                until_date=until_date,
                set_spec=set_spec,
            )
            yield page
            if page["isLastPage"]:
                break
            token = page["resumptionToken"]
            time.sleep(self.request_delay)

    def harvest_all(
        self,
        from_date:  str | None = None,
        until_date: str | None = None,
        set_spec:   str | None = None,
    ) -> Generator[Any, None, None]:
        """
        Stream every record one at a time, following resumption tokens.

        The filter / enrich / transform pipeline is applied to each record.
        Dropped records are never yielded.
        Memory footprint is always bounded to a single page.

        Yields
        ------
        Parsed (and optionally filtered / enriched / transformed) record.
        """
        for page in self.harvest_pages(
            from_date=from_date, until_date=until_date, set_spec=set_spec
        ):
            yield from page["records"]

    # ------------------------------------------------------------------
    # Record parser (public — usable on any <record> element you already have)
    # ------------------------------------------------------------------

    def parse_record(self, record: ET.Element) -> dict[str, Any]:
        """
        Parse a single OAI-PMH <record> element into a structured dict.

        Auto-detects the metadata namespace (DataCite kernel-4 or Dublin
        Core) and dispatches to the right sub-parser.  Every XML attribute
        is captured alongside the element's text value.

        Parameters
        ----------
        record : ET.Element
            A <record> element, e.g. from
            xml_tree.iter('{http://www.openarchives.org/OAI/2.0/}record').

        Returns
        -------
        dict — {"header": {...}, "metadata": {...}}
        """
        result: dict[str, Any] = {}

        header_el = record.find(f"{_NS_OAI}header")
        result["header"] = self._parse_header(header_el) if header_el is not None else {}

        metadata_el = record.find(f"{_NS_OAI}metadata")
        if metadata_el is None:
            result["metadata"] = None
            return result

        # Dispatch by namespace — DataCite first, then Dublin Core
        resource_el = metadata_el.find(f"{_NS_DC4}resource")
        if resource_el is not None:
            result["metadata"] = self._parse_datacite_resource(resource_el)
            return result

        oai_dc_el = metadata_el.find(f"{_NS_OAI_DC}dc")
        if oai_dc_el is not None:
            result["metadata"] = self._parse_oai_dc(oai_dc_el)
            return result

        # Unknown format — raw XML fallback so nothing is silently lost
        result["metadata"] = {"raw": ET.tostring(metadata_el, encoding="unicode")}
        return result

    # ------------------------------------------------------------------
    # Pipeline application
    # ------------------------------------------------------------------

    def _apply_pipeline(self, record: dict) -> Any | None:
        """
        Run filter → enrich → transform on one parsed record.

        Returns the (possibly transformed) record, or None if dropped.
        Exceptions in any hook are logged and the record is kept/passed as-is.
        """
        # 1. Filter
        if self.filter_fn is not None:
            try:
                if not self.filter_fn(record):
                    return None
            except Exception as exc:
                logger.warning(
                    "filter_fn raised %s for %s — keeping record.",
                    exc, record.get("header", {}).get("identifier"),
                )

        # 2. Enrich
        if self.enrich_fn is not None:
            try:
                record = self.enrich_fn(record)
            except Exception as exc:
                logger.warning(
                    "enrich_fn raised %s for %s — using un-enriched record.",
                    exc, record.get("header", {}).get("identifier"),
                )

        # 3. Transform
        if self.transform_fn is not None:
            try:
                return self.transform_fn(record)
            except Exception as exc:
                logger.warning(
                    "transform_fn raised %s for %s — returning un-transformed record.",
                    exc, record.get("header", {}).get("identifier"),
                )

        return record

    @staticmethod
    def _empty_page() -> dict[str, Any]:
        return {
            "records":          [],
            "resumptionToken":  None,
            "cursor":           None,
            "completeListSize": None,
            "isLastPage":       True,
            "pageNumber":       0,
            "pageRecordCount":  0,
            "schemaReport":     None,
        }

    # ------------------------------------------------------------------
    # HTTP layer
    # ------------------------------------------------------------------

    def _request(self, params: dict[str, str]) -> ET.Element:
        """Single HTTP GET with exponential back-off retry."""
        url      = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug("GET %s  (attempt %d/%d)", url, attempt, self.max_retries)
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    raw = resp.read()
                tree = ET.fromstring(raw)
                self._check_oai_error(tree)
                return tree
            except OAIError:
                raise                           # no retry on OAI-level errors
            except ET.ParseError as exc:
                raise OAIHarvestError(f"XML parse error for {url}: {exc}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                wait = self.retry_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Request failed (attempt %d/%d): %s — retry in %.0fs",
                    attempt, self.max_retries, exc, wait,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

        raise OAIHarvestError(
            f"All {self.max_retries} attempt(s) failed for {url}"
        ) from last_exc

    def _check_oai_error(self, tree: ET.Element) -> None:
        for err in tree.iter(f"{_NS_OAI}error"):
            raise OAIError(
                code=err.get("code", "unknown"),
                message=(err.text or "").strip(),
            )

    # ------------------------------------------------------------------
    # Namespace-aware element helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _text(el: ET.Element | None) -> str | None:
        if el is None:
            return None
        t = (el.text or "").strip()
        return t if t else None

    @staticmethod
    def _find(parent: ET.Element, local: str, ns: str = _NS_OAI) -> ET.Element | None:
        return parent.find(f"{ns}{local}")

    @staticmethod
    def _findall(parent: ET.Element, local: str, ns: str = _NS_OAI) -> list[ET.Element]:
        return parent.findall(f"{ns}{local}")

    @staticmethod
    def _resumption_token(list_el: ET.Element) -> str | None:
        tok = list_el.find(f"{_NS_OAI}resumptionToken")
        if tok is None:
            return None
        t = (tok.text or "").strip()
        return t if t else None

    def _base_params(
        self,
        verb:       str,
        from_date:  str | None,
        until_date: str | None,
        set_spec:   str | None,
    ) -> dict[str, str]:
        p: dict[str, str] = {"verb": verb, "metadataPrefix": self.metadata_prefix}
        if from_date:  p["from"]  = from_date
        if until_date: p["until"] = until_date
        if set_spec:   p["set"]   = set_spec
        return p

    # ------------------------------------------------------------------
    # OAI-PMH header
    # ------------------------------------------------------------------

    def _parse_header(self, header: ET.Element) -> dict[str, Any]:
        return {
            "identifier": self._text(self._find(header, "identifier")),
            "datestamp":  self._text(self._find(header, "datestamp")),
            "setSpecs":   [self._text(s) for s in self._findall(header, "setSpec")],
            "status":     header.get("status"),     # "deleted" | None
        }

    # ------------------------------------------------------------------
    # Dublin Core parser  (oai_dc)
    # ------------------------------------------------------------------

    def _parse_oai_dc(self, dc_el: ET.Element) -> dict[str, Any]:
        def _all(tag: str) -> list[str | None]:
            return [self._text(e) for e in dc_el.findall(f"{_NS_DC}{tag}")]

        return {
            "metadataFormat": "oai_dc",
            "titles":         _all("title"),
            "creators":       _all("creator"),
            "subjects":       _all("subject"),
            "descriptions":   _all("description"),
            "publishers":     _all("publisher"),
            "contributors":   _all("contributor"),
            "dates":          _all("date"),
            "types":          _all("type"),
            "formats":        _all("format"),
            "identifiers":    _all("identifier"),
            "sources":        _all("source"),
            "languages":      _all("language"),
            "relations":      _all("relation"),
            "coverages":      _all("coverage"),
            "rights":         _all("rights"),
        }

    # ------------------------------------------------------------------
    # DataCite kernel-4 parser
    # ------------------------------------------------------------------

    def _parse_datacite_resource(self, resource: ET.Element) -> dict[str, Any]:
        def _f(tag: str)  -> ET.Element | None:  return resource.find(f"{_NS_DC4}{tag}")
        def _fa(tag: str) -> list[ET.Element]:    return resource.findall(f"{_NS_DC4}{tag}")

        meta: dict[str, Any] = {"metadataFormat": "oai_datacite"}

        id_el = _f("identifier")
        meta["identifier"] = {
            "value":          self._text(id_el),
            "identifierType": id_el.get("identifierType") if id_el is not None else None,
        }

        creators_el = _f("creators")
        meta["creators"] = (
            [self._parse_dc4_creator(c) for c in creators_el.findall(f"{_NS_DC4}creator")]
            if creators_el is not None else []
        )

        titles_el = _f("titles")
        meta["titles"] = (
            [self._parse_dc4_title(t) for t in titles_el.findall(f"{_NS_DC4}title")]
            if titles_el is not None else []
        )

        pub_el = _f("publisher")
        meta["publisher"] = {
            "value":                     self._text(pub_el),
            "publisherIdentifier":       pub_el.get("publisherIdentifier")       if pub_el is not None else None,
            "publisherIdentifierScheme": pub_el.get("publisherIdentifierScheme") if pub_el is not None else None,
            "schemeURI":                 pub_el.get("schemeURI")                 if pub_el is not None else None,
            "lang":                      pub_el.get(f"{_NS_XML}lang")            if pub_el is not None else None,
        }

        meta["publicationYear"] = self._text(_f("publicationYear"))

        rt_el = _f("resourceType")
        meta["resourceType"] = {
            "value":               self._text(rt_el),
            "resourceTypeGeneral": rt_el.get("resourceTypeGeneral") if rt_el is not None else None,
        }

        subjects_el = _f("subjects")
        meta["subjects"] = (
            [self._parse_dc4_subject(s) for s in subjects_el.findall(f"{_NS_DC4}subject")]
            if subjects_el is not None else []
        )

        contribs_el = _f("contributors")
        meta["contributors"] = (
            [self._parse_dc4_contributor(c) for c in contribs_el.findall(f"{_NS_DC4}contributor")]
            if contribs_el is not None else []
        )

        dates_el = _f("dates")
        meta["dates"] = (
            [self._parse_dc4_date(d) for d in dates_el.findall(f"{_NS_DC4}date")]
            if dates_el is not None else []
        )

        meta["language"] = self._text(_f("language"))

        alt_el = _f("alternateIdentifiers")
        meta["alternateIdentifiers"] = []
        if alt_el is not None:
            for ai in alt_el.findall(f"{_NS_DC4}alternateIdentifier"):
                meta["alternateIdentifiers"].append({
                    "value":                   self._text(ai),
                    "alternateIdentifierType": ai.get("alternateIdentifierType"),
                })

        ri_con = _f("relatedIdentifiers")
        meta["relatedIdentifiers"] = (
            [self._parse_dc4_related_identifier(ri)
             for ri in ri_con.findall(f"{_NS_DC4}relatedIdentifier")]
            if ri_con is not None else []
        )

        sizes_el = _f("sizes")
        meta["sizes"] = (
            [self._text(s) for s in sizes_el.findall(f"{_NS_DC4}size")]
            if sizes_el is not None else []
        )

        fmts_el = _f("formats")
        meta["formats"] = (
            [self._text(f) for f in fmts_el.findall(f"{_NS_DC4}format")]
            if fmts_el is not None else []
        )

        meta["version"] = self._text(_f("version"))

        rights_el = _f("rightsList")
        meta["rightsList"] = (
            [self._parse_dc4_rights(r) for r in rights_el.findall(f"{_NS_DC4}rights")]
            if rights_el is not None else []
        )

        descs_el = _f("descriptions")
        meta["descriptions"] = (
            [self._parse_dc4_description(d) for d in descs_el.findall(f"{_NS_DC4}description")]
            if descs_el is not None else []
        )

        geo_el = _f("geoLocations")
        meta["geoLocations"] = (
            [self._parse_dc4_geo_location(g) for g in geo_el.findall(f"{_NS_DC4}geoLocation")]
            if geo_el is not None else []
        )

        funding_el = _f("fundingReferences")
        meta["fundingReferences"] = (
            [self._parse_dc4_funding_reference(fr)
             for fr in funding_el.findall(f"{_NS_DC4}fundingReference")]
            if funding_el is not None else []
        )

        rel_items_el = _f("relatedItems")
        meta["relatedItems"] = (
            [self._parse_dc4_related_item(ri)
             for ri in rel_items_el.findall(f"{_NS_DC4}relatedItem")]
            if rel_items_el is not None else []
        )

        return meta

    # ------------------------------------------------------------------
    # DataCite sub-element parsers
    # ------------------------------------------------------------------

    def _parse_dc4_name_identifiers(self, parent: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "value":                (ni.text or "").strip() or None,
                "nameIdentifierScheme": ni.get("nameIdentifierScheme"),
                "schemeURI":            ni.get("schemeURI"),
            }
            for ni in parent.findall(f"{_NS_DC4}nameIdentifier")
        ]

    def _parse_dc4_affiliations(self, parent: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "value":                       self._text(aff),
                "affiliationIdentifier":       aff.get("affiliationIdentifier"),
                "affiliationIdentifierScheme": aff.get("affiliationIdentifierScheme"),
                "schemeURI":                   aff.get("schemeURI"),
            }
            for aff in parent.findall(f"{_NS_DC4}affiliation")
        ]

    def _parse_dc4_creator(self, creator: ET.Element) -> dict[str, Any]:
        name_el = creator.find(f"{_NS_DC4}creatorName")
        return {
            "creatorName": {
                "value":    self._text(name_el),
                "nameType": name_el.get("nameType") if name_el is not None else None,
            },
            "givenName":       self._text(creator.find(f"{_NS_DC4}givenName")),
            "familyName":      self._text(creator.find(f"{_NS_DC4}familyName")),
            "nameIdentifiers": self._parse_dc4_name_identifiers(creator),
            "affiliations":    self._parse_dc4_affiliations(creator),
        }

    def _parse_dc4_contributor(self, contributor: ET.Element) -> dict[str, Any]:
        name_el = contributor.find(f"{_NS_DC4}contributorName")
        return {
            "contributorType": contributor.get("contributorType"),
            "contributorName": {
                "value":    self._text(name_el),
                "nameType": name_el.get("nameType") if name_el is not None else None,
            },
            "givenName":       self._text(contributor.find(f"{_NS_DC4}givenName")),
            "familyName":      self._text(contributor.find(f"{_NS_DC4}familyName")),
            "nameIdentifiers": self._parse_dc4_name_identifiers(contributor),
            "affiliations":    self._parse_dc4_affiliations(contributor),
        }

    def _parse_dc4_title(self, title_el: ET.Element) -> dict[str, Any]:
        return {
            "value":     self._text(title_el),
            "titleType": title_el.get("titleType"),
            "lang":      title_el.get(f"{_NS_XML}lang"),
        }

    def _parse_dc4_subject(self, subject_el: ET.Element) -> dict[str, Any]:
        return {
            "value":         self._text(subject_el),
            "subjectScheme": subject_el.get("subjectScheme"),
            "schemeURI":     subject_el.get("schemeURI"),
            "valueURI":      subject_el.get("valueURI"),
            "lang":          subject_el.get(f"{_NS_XML}lang"),
        }

    def _parse_dc4_date(self, date_el: ET.Element) -> dict[str, Any]:
        return {
            "value":           self._text(date_el),
            "dateType":        date_el.get("dateType"),
            "dateInformation": date_el.get("dateInformation"),
        }

    def _parse_dc4_related_identifier(self, ri_el: ET.Element) -> dict[str, Any]:
        return {
            "value":                 self._text(ri_el),
            "relationType":          ri_el.get("relationType"),
            "relatedIdentifierType": ri_el.get("relatedIdentifierType"),
            "relatedMetadataScheme": ri_el.get("relatedMetadataScheme"),
            "schemeURI":             ri_el.get("schemeURI"),
            "schemeType":            ri_el.get("schemeType"),
            "resourceTypeGeneral":   ri_el.get("resourceTypeGeneral"),
        }

    def _parse_dc4_rights(self, rights_el: ET.Element) -> dict[str, Any]:
        raw = (rights_el.text or "").strip()
        return {
            "value":                  html.unescape(raw) if raw else None,
            "rightsURI":              rights_el.get("rightsURI"),
            "rightsIdentifier":       rights_el.get("rightsIdentifier"),
            "rightsIdentifierScheme": rights_el.get("rightsIdentifierScheme"),
            "schemeURI":              rights_el.get("schemeURI"),
            "lang":                   rights_el.get(f"{_NS_XML}lang"),
        }

    def _parse_dc4_description(self, desc_el: ET.Element) -> dict[str, Any]:
        raw = (desc_el.text or "").strip()
        return {
            "value":           html.unescape(raw) if raw else None,
            "descriptionType": desc_el.get("descriptionType"),
            "lang":            desc_el.get(f"{_NS_XML}lang"),
        }

    def _parse_dc4_geo_location(self, geo_el: ET.Element) -> dict[str, Any]:
        def _pt(parent: ET.Element | None) -> dict[str, str | None] | None:
            if parent is None:
                return None
            return {
                "pointLongitude": self._text(parent.find(f"{_NS_DC4}pointLongitude")),
                "pointLatitude":  self._text(parent.find(f"{_NS_DC4}pointLatitude")),
            }

        result: dict[str, Any] = {
            "geoLocationPlace":    self._text(geo_el.find(f"{_NS_DC4}geoLocationPlace")),
            "geoLocationPoint":    _pt(geo_el.find(f"{_NS_DC4}geoLocationPoint")),
            "geoLocationBox":      None,
            "geoLocationPolygons": [],
        }
        box_el = geo_el.find(f"{_NS_DC4}geoLocationBox")
        if box_el is not None:
            result["geoLocationBox"] = {
                "westBoundLongitude": self._text(box_el.find(f"{_NS_DC4}westBoundLongitude")),
                "eastBoundLongitude": self._text(box_el.find(f"{_NS_DC4}eastBoundLongitude")),
                "southBoundLatitude": self._text(box_el.find(f"{_NS_DC4}southBoundLatitude")),
                "northBoundLatitude": self._text(box_el.find(f"{_NS_DC4}northBoundLatitude")),
            }
        for poly in geo_el.findall(f"{_NS_DC4}geoLocationPolygon"):
            polygon: dict[str, Any] = {"polygonPoints": [], "inPolygonPoint": None}
            for pp in poly.findall(f"{_NS_DC4}polygonPoint"):
                polygon["polygonPoints"].append(_pt(pp))
            polygon["inPolygonPoint"] = _pt(poly.find(f"{_NS_DC4}inPolygonPoint"))
            result["geoLocationPolygons"].append(polygon)
        return result

    def _parse_dc4_funding_reference(self, fr_el: ET.Element) -> dict[str, Any]:
        fi_el = fr_el.find(f"{_NS_DC4}funderIdentifier")
        aw_el = fr_el.find(f"{_NS_DC4}awardNumber")
        return {
            "funderName": self._text(fr_el.find(f"{_NS_DC4}funderName")),
            "funderIdentifier": {
                "value":                (fi_el.text or "").strip() or None,
                "funderIdentifierType": fi_el.get("funderIdentifierType"),
                "schemeURI":            fi_el.get("schemeURI"),
            } if fi_el is not None else None,
            "awardNumber": {
                "value":    self._text(aw_el),
                "awardURI": aw_el.get("awardURI") if aw_el is not None else None,
            } if aw_el is not None else None,
            "awardTitle": self._text(fr_el.find(f"{_NS_DC4}awardTitle")),
        }

    def _parse_dc4_related_item(self, ri: ET.Element) -> dict[str, Any]:
        ri_id_el    = ri.find(f"{_NS_DC4}relatedItemIdentifier")
        num_el      = ri.find(f"{_NS_DC4}number")
        creators_el = ri.find(f"{_NS_DC4}creators")
        titles_el   = ri.find(f"{_NS_DC4}titles")
        contribs_el = ri.find(f"{_NS_DC4}contributors")
        return {
            "relationType":    ri.get("relationType"),
            "relatedItemType": ri.get("relatedItemType"),
            "relatedItemIdentifier": {
                "value":                     self._text(ri_id_el),
                "relatedItemIdentifierType": ri_id_el.get("relatedItemIdentifierType"),
                "relatedMetadataScheme":     ri_id_el.get("relatedMetadataScheme"),
                "schemeURI":                 ri_id_el.get("schemeURI"),
                "schemeType":                ri_id_el.get("schemeType"),
            } if ri_id_el is not None else None,
            "creators": (
                [self._parse_dc4_creator(c) for c in creators_el.findall(f"{_NS_DC4}creator")]
                if creators_el is not None else []
            ),
            "titles": (
                [self._parse_dc4_title(t) for t in titles_el.findall(f"{_NS_DC4}title")]
                if titles_el is not None else []
            ),
            "publicationYear": self._text(ri.find(f"{_NS_DC4}publicationYear")),
            "volume":          self._text(ri.find(f"{_NS_DC4}volume")),
            "issue":           self._text(ri.find(f"{_NS_DC4}issue")),
            "number": {
                "value":      self._text(num_el),
                "numberType": num_el.get("numberType") if num_el is not None else None,
            } if num_el is not None else None,
            "firstPage":  self._text(ri.find(f"{_NS_DC4}firstPage")),
            "lastPage":   self._text(ri.find(f"{_NS_DC4}lastPage")),
            "publisher":  self._text(ri.find(f"{_NS_DC4}publisher")),
            "edition":    self._text(ri.find(f"{_NS_DC4}edition")),
            "contributors": (
                [self._parse_dc4_contributor(c) for c in contribs_el.findall(f"{_NS_DC4}contributor")]
                if contribs_el is not None else []
            ),
        }

    # ------------------------------------------------------------------
    # Generic fallback: XML element → nested dict
    # ------------------------------------------------------------------

    @staticmethod
    def _elem_to_dict(el: ET.Element) -> dict[str, Any]:
        node: dict[str, Any] = {"tag": el.tag, "attrs": dict(el.attrib)}
        text = (el.text or "").strip()
        if text:
            node["text"] = text
        children = [OAIHarvester._elem_to_dict(c) for c in el]
        if children:
            node["children"] = children
        return node

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        hooks = ", ".join(
            name for name, fn in [
                ("filter_fn",    self.filter_fn),
                ("enrich_fn",    self.enrich_fn),
                ("transform_fn", self.transform_fn),
                ("schema_fn",    self.schema_fn),
            ] if fn is not None
        )
        return (
            f"{self.__class__.__name__}("
            f"base_url={self.base_url!r}, "
            f"metadata_prefix={self.metadata_prefix!r}"
            + (f", hooks=[{hooks}]" if hooks else "")
            + f", total_harvested={self._total_harvested})"
        )



