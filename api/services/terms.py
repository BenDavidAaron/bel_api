import elasticsearch
import falcon
from typing import Mapping, List
import re

import bel.db.elasticsearch
import bel.db.arangodb

from bel.Config import config

import logging
log = logging.getLogger(__name__)

es = bel.db.elasticsearch.get_client()

arangodb_client = bel.db.arangodb.get_client()
belns_db = bel.db.arangodb.get_belns_handle(arangodb_client)


def get_species_info(species_id):

    log.debug(species_id)

    url_template = "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?mode=Info&lvl=3&lin=f&keep=1&srchmode=1&unlock&id=<src_id>"
    search_body = {
        "_source": ["src_id", "id", "name", "label", "taxonomy_rank"],
        "query": {
            "term": {"id": species_id}
        }
    }

    result = es.search(index='terms', doc_type='term', body=search_body)
    src = result['hits']['hits'][0]['_source']
    url = re.sub('(<src_id>)', src['src_id'], url_template)
    src['url'] = url
    del src['src_id']
    return src


def get_species_object(species_id):

    species = get_species_info(species_id)
    return {"id": species['id'], "label": species['label']}


def get_term(term_id):
    """Get term using term_id

    Term ID has to match either the id or the alt_ids
    """
    search_body = {
        "query": {
            "bool": {
                "should": [
                    {"term": {"id": term_id}},
                    {"term": {"alt_ids": term_id}},
                    {"term": {"obsolete_ids": term_id}},
                ]
            }
        }
    }

    result = es.search(index='terms', doc_type='term', body=search_body)
    # import json
    # print('DumpVar:\n', json.dumps(result, indent=4))
    if len(result['hits']['hits']) > 0:
        result = result['hits']['hits'][0]['_source']
    else:
        result = None

    return result


def get_term_search(search_term, size, entity_types, annotation_types, species, namespaces):
    """Search for terms given search term"""

    if not size:
        size = 10

    filters = []
    if entity_types:
        filters.append({"terms": {"entity_types": entity_types}})
    if annotation_types:
        filters.append({"terms": {"annotation_types": annotation_types}})
    if species:
        filters.append({"terms": {"species": species}})
    if namespaces:
        filters.append({"terms": {"namespaces": namespaces}})

    search_body = {
        "size": size,
        "query": {
            "bool": {
                "minimum_should_match": 1,
                "should": [
                    {
                        "match": {
                            "id": {
                                "query": "AKT1",
                                "boost": 4
                            }
                        }
                    },
                    {
                        "match": {
                            "name": {
                                "query": "AKT1",
                                "boost": 2
                            }
                        }
                    },
                    {
                        "match": {
                            "synonyms": {
                                "query": "AKT1"
                            }
                        }
                    },
                    {
                        "match": {
                            "label": {
                                "query": "AKT1",
                                "boost": 4
                            }
                        }
                    },
                    {
                        "match": {
                            "alt_ids": {
                                "query": "AKT1",
                                "boost": 2
                            }
                        }
                    },
                    {
                        "match": {
                            "src_id": {
                                "query": "AKT1"
                            }
                        }
                    }
                ],
                "filter": filters,
            }
        },
        "highlight": {
            "fields": [
                {"id": {}},
                {"name": {}},
                {"label": {}},
                {"synonyms": {}},
                {"alt_ids": {}},
                {"src_id": {}}
            ]
        }
    }

    results = es.search(index='terms', doc_type='term', body=search_body)

    search_results = []
    for result in results['hits']['hits']:
        search_results.append(result['_source'] + {'highlight': result['highlight']})

    return search_results


def get_term_completions(completion_text, size, entity_types, annotation_types, species, namespaces):
    """Get Term completions filtered by additional requirements

    Args:
        completion_text: text to complete to location NSArgs
        size: how many terms to return
        entity_types: list of entity_types used to filter completion results
        annotation_types: list of annotation types used to filter completion results
        species: list of species (TAX:nnnn) used to filter completions
        namespaces: list of namespaces to filter completions

    Returns:
        list of NSArgs
    """

    if isinstance(entity_types, str):
        entity_types = [entity_types]
    if isinstance(annotation_types, str):
        annotation_types = [annotation_types]
    if isinstance(species, str):
        species = [species]
    if isinstance(namespaces, str):
        namespaces = [namespaces]

    filters = []
    if entity_types:
        filters.append({"terms": {"entity_types": entity_types}})
    if annotation_types:
        filters.append({"terms": {"annotation_types": annotation_types}})
    if species:
        filters.append({"terms": {"species_id": species}})
    if namespaces:
        filters.append({"terms": {"namespace": namespaces}})

    search_body = {
        "_source": ["id", "name", "label", "description", "species_id", "species_label", "entity_types", "annotation_types"],
        "size": 10,
        "query": {
            "bool": {
                "must": {
                    "match": {
                        "autocomplete": {
                            "query": completion_text,
                            "operator": "and"
                        }
                    }
                },
                "filter": filters,
            }
        },
        "highlight": {
            "fields": {
                "autocomplete": {"type": "plain"}
            }
        }
    }

    # import json
    # print('DumpVar:\n', json.dumps(search_body, indent=4))
    results = es.search(index='terms', doc_type='term', body=search_body)

    # highlight matches
    completions = []

    for result in results['hits']['hits']:
        species_id = result['_source'].get('species_id', None)
        species_label = result['_source'].get('species_label', None)
        species = {'id': species_id, 'label': species_label}

        # Filter out duplicate matches
        matches = []
        matches_lower = []
        for match in result["highlight"]["autocomplete"]:
            if match.lower() in matches_lower:
                continue
            matches.append(match)
            matches_lower.append(match.lower())

        completions.append({
            "id": result['_source']["id"],
            "name": result['_source']['name'],
            "label": result['_source']['label'],
            "description": result['_source'].get('description', None),
            "species": species,
            "highlight": matches,
        })

    return completions


def term_types():
    """Collect Term Types and their counts

    Return aggregations of namespaces, entity types, and context types
    up to a 100 of each type (see size=<number> in query below)

    Returns:
        Mapping[str, Mapping[str, int]]: dict of dicts for term types
    """

    size = 100

    search_body = {
        "aggs": {
            "namespace_term_counts": {"terms": {"field": "namespace", "size": size}},
            "entity_type_counts": {"terms": {"field": "entity_types", "size": size}},
            "annotation_type_counts": {"terms": {"field": "annotation_types", "size": size}},
        }
    }

    results = es.search(index='terms', doc_type='term', body=search_body, size=0)

    types = {'namespaces': {}, 'entity_types': {}, 'annotation_types': {}}

    aggs = {
        "namespace_term_counts": "namespaces",
        "entity_type_counts": "entity_types",
        "annotation_type_counts": "annotation_types",
    }
    for agg in aggs:
        for bucket in results['aggregations'][agg]['buckets']:
            types[aggs[agg]][bucket['key']] = bucket['doc_count']

    return types


def namespace_term_counts():
    """Generate counts of each namespace in terms index

    This function is at least used in the /status endpoint to show how many
    terms are in each namespace and what namespaces are available.

    Returns:
        List[Mapping[str, int]]: array of namespace vs counts
    """

    search_body = {
        "aggs": {
            "namespace_term_counts": {"terms": {"field": "namespace"}}
        }
    }

    # Get term counts but raise error if elasticsearch is not available
    try:
        results = es.search(index='terms', doc_type='term', body=search_body, size=0)
        results = results['aggregations']['namespace_term_counts']['buckets']
    except elasticsearch.ConnectionError as e:
        raise falcon.HTTPBadRequest(
            title='Connection Refused',
            description='Cannot access Elasticsearch',
        )

    # return results
    return [{'namespace': r['key'], 'count': r['doc_count']} for r in results]


def get_equivalents(term_id: str, namespaces: List[str]=None) -> List[Mapping[str, str]]:
    """Get equivalents given ns:id and target namespaces

    The target_namespaces list in the argument dictionary is ordered by priority.

    Args:
        term_id (str): term id
        namespaces (Mapping[str, Any]): filter resulting equivalents to listed namespaces, ordered by priority

    Returns:
        List[Mapping[str, str]]: e.g. [{'term_id': 'HGNC:5', 'namespace': 'EG'}]
    """

    term_id_key = bel.db.arangodb.arango_id_to_key(term_id)

    query = f"FOR vertex, edge IN 1..10 ANY 'equivalence_nodes/{term_id_key}' equivalence_edges " + "RETURN {term_id: vertex._key, namespace: vertex.namespace}"
    cursor = belns_db.aql.execute(query)

    equivalents = {}
    for record in cursor:
        equivalents[record['namespace']] = record['term_id']

    return equivalents


def canonicalize(term_id: str, namespace_targets: Mapping[str, List[str]] = None) -> str:
    """Canonicalize term_id

    Convert term namespace to canonical namespaces pulling them from
    the settings ArangoDB collection (e.g. the API configured canonical
    namespace mappings) if not given. The target namespaces are ordered
    and the first namespace:id found will be returned.

    For example, given HGNC:A1BG, this function will return EG:1 if
    namespace_targets={'HGNC': [EG', 'SP']}

    Args:
        term_id (str): term to canonicalize
        namespace_targets (Mapping[str, List[str]]): Map of namespace targets to convert term into

    Returns:
        str: return canonicalized term if available, else the original term_id
    """

    if not namespace_targets:
        namespace_targets = config['bel']['lang']['canonical']

    for start_ns in namespace_targets:
        if re.match(start_ns, term_id):
            equivalents = get_equivalents(term_id)
            # log.info(f'Equiv: {equivalents}')
            for target_ns in namespace_targets[start_ns]:
                if target_ns in equivalents:
                    term_id = equivalents[target_ns]
                    break

    return term_id


def decanonicalize(term_id: str, namespace_targets: Mapping[str, List[str]] = None) -> str:
    """De-canonicalize term_id

    Convert term namespace to user friendly namespaces pulling them from
    the settings ArangoDB collection (e.g. the API configured decanonical
    namespace mappings) if not given. The target namespaces are ordered
    and the first namespace:id found will be returned.

    For example, given EG:1, this function will return HGNC:A1BG if
    namespace_targets={'EG': [HGNC', 'MGI', 'RGD', 'SP'], 'SP': [HGNC', 'MGI', 'RGD']}

    Args:
        term_id (str): term to decanonicalize
        namespace_targets (Mapping[str, List[str]]): Map of namespace targets to convert term into

    Returns:
        str: return decanonicalized term if available, else the original term_id
    """

    if not namespace_targets:
        namespace_targets = config['bel']['lang']['decanonical']

    for start_ns in namespace_targets:
        if re.match(start_ns, term_id):
            equivalents = get_equivalents(term_id)
            log.debug(f'Term: {term_id} Equiv: {equivalents}')
            for target_ns in namespace_targets[start_ns]:
                log.debug(f'Checking target namespace: {target_ns}')
                if target_ns in equivalents:
                    term_id = equivalents[target_ns]
                    break

    return term_id
