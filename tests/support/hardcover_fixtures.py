"""Irreversibly synthetic fixtures for the F09b Hardcover live-shape contract."""

from __future__ import annotations


OBSERVED_LIVE_SHAPE_STATS = {
    "depth": 11,
    "max_list_items": 3,
    "max_object_keys": 34,
    "max_string_length": 158,
    "nodes": 107,
}


def hardcover_live_search_payload(
    requested_title: str, result_title: str | None
) -> dict[str, object]:
    """Return the sanitized 2026-07-14 live key/type shape with synthetic values."""

    hits: list[object] = []
    found = 0
    if result_title is not None:
        found = 3
        hits.append(
            {
                "document": {
                    "activities_count": 12,
                    "alternative_titles": ["Synthetic Alternate Title"],
                    "author_names": ["Synthetic Author", "Synthetic Contributor"],
                    "book_category_id": 1,
                    "compilation": False,
                    "content_warnings": [],
                    "contribution_types": ["Author", "Editor"],
                    "contributions": [
                        {
                            "author": {
                                "id": 101,
                                "image": {},
                                "name": "Synthetic Author",
                                "slug": "synthetic-author",
                            }
                        },
                        {
                            "author": {
                                "id": 102,
                                "image": {},
                                "name": "Synthetic Contributor",
                                "slug": "synthetic-contributor",
                            },
                            "contribution": "Editor",
                        },
                    ],
                    "cover_color": "#123456",
                    "description": "S" * 158,
                    "featured_series": {},
                    "genres": [],
                    "has_audiobook": True,
                    "has_ebook": True,
                    "id": "synthetic-live-book-id",
                    "image": {
                        "color": "#123456",
                        "color_name": "Synthetic Blue",
                        "height": 900,
                        "id": 103,
                        "url": "https://example.invalid/synthetic-cover.jpg",
                        "width": 600,
                    },
                    "isbns": ["synthetic-isbn-a", "synthetic-isbn-b"],
                    "lists_count": 4,
                    "moods": [],
                    "pages": 320,
                    "prompts_count": 2,
                    "rating": 4.25,
                    "ratings_count": 42,
                    "release_date": "2000-01-01",
                    "release_year": 2000,
                    "reviews_count": 8,
                    "series_ids": [],
                    "series_names": [],
                    "slug": "synthetic-live-book",
                    "subtitle": "A Synthetic Subtitle",
                    "tags": [],
                    "title": result_title,
                    "users_count": 64,
                    "users_read_count": 32,
                },
                "highlight": {
                    "title": {
                        "matched_tokens": ["synthetic", "book", "title"],
                        "snippet": "<mark>Synthetic</mark> Book Title",
                    }
                },
                "highlights": [
                    {
                        "field": "title",
                        "matched_tokens": ["synthetic", "book", "title"],
                        "snippet": "<mark>Synthetic</mark> Book Title",
                    }
                ],
                "text_match": 123456,
                "text_match_info": {
                    "best_field_score": "123456",
                    "best_field_weight": 1,
                    "fields_matched": 1,
                    "num_tokens_dropped": 0,
                    "score": "123456",
                    "tokens_matched": 3,
                    "typo_prefix_score": 0,
                },
            }
        )
    return {
        "data": {
            "search": {
                "error": None,
                "page": 1,
                "per_page": 1,
                "query": requested_title,
                "query_type": "Book",
                "results": {
                    "facet_counts": [],
                    "found": found,
                    "hits": hits,
                    "out_of": 100,
                    "page": 1,
                    "request_params": {
                        "collection_name": "synthetic-books",
                        "first_q": requested_title,
                        "per_page": 1,
                        "q": requested_title,
                    },
                    "search_cutoff": False,
                    "search_time_ms": 2,
                },
            }
        }
    }


__all__ = ["OBSERVED_LIVE_SHAPE_STATS", "hardcover_live_search_payload"]
