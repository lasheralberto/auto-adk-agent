import json
from pathlib import Path
from typing import Any

import requests


STRAVA_API_BASE_URL = 'https://www.strava.com/api/v3'
DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 30
DEFAULT_TIMEOUT = 30


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        return ','.join(str(item) for item in value)
    return value


def _clean_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _normalize_value(value)
        for key, value in values.items()
        if value is not None
    }


def _build_headers(access_token: str, accept: str = 'application/json') -> dict[str, str]:
    if not access_token or not access_token.strip():
        raise ValueError('access_token cannot be empty')

    return {
        'Authorization': f'Bearer {access_token.strip()}',
        'Accept': accept,
    }


def _parse_json_object_arg(arg_name: str, arg_value: str | None) -> dict[str, Any]:
    if not arg_value or not arg_value.strip():
        return {}

    try:
        parsed = json.loads(arg_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f'{arg_name} must be valid JSON: {exc}') from exc

    if not isinstance(parsed, dict):
        raise ValueError(f'{arg_name} must decode to a JSON object')

    return parsed


def _parse_csv_arg(arg_value: str) -> list[str]:
    if not arg_value or not arg_value.strip():
        raise ValueError('keys cannot be empty')

    values = [item.strip() for item in arg_value.split(',') if item.strip()]
    if not values:
        raise ValueError('keys cannot be empty')
    return values


def _strava_request(
    method: str,
    path: str,
    access_token: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    accept: str = 'application/json',
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f'{STRAVA_API_BASE_URL}{path}'
    response = requests.request(
        method=method,
        url=url,
        headers=_build_headers(access_token, accept=accept),
        params=_clean_mapping(params or {}),
        data=_clean_mapping(data or {}),
        json=json_body,
        files=files,
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    content_type = response.headers.get('Content-Type', '')
    if 'application/json' in content_type:
        content = response.json()
    else:
        content = response.text

    return {
        'status': 'success',
        'method': method.upper(),
        'path': path,
        'content_type': content_type,
        'data': content,
    }


def get_athlete_stats(access_token: str, athlete_id: int) -> str:
    """Get authenticated athlete stats by athlete id."""
    return _serialize_payload(
        _strava_request('GET', f'/athletes/{athlete_id}/stats', access_token)
    )


def get_logged_in_athlete(access_token: str) -> str:
    """Get the authenticated athlete profile."""
    return _serialize_payload(_strava_request('GET', '/athlete', access_token))


def update_logged_in_athlete_weight(access_token: str, weight_kg: float) -> str:
    """Update the authenticated athlete weight. Requires profile:write."""
    return _serialize_payload(
        _strava_request('PUT', '/athlete', access_token, data={'weight': weight_kg})
    )


def get_logged_in_athlete_zones(access_token: str) -> str:
    """Get the authenticated athlete heart rate and power zones. Requires profile:read_all."""
    return _serialize_payload(_strava_request('GET', '/athlete/zones', access_token))


def get_segment_by_id(access_token: str, segment_id: int) -> str:
    """Get a segment by id."""
    return _serialize_payload(_strava_request('GET', f'/segments/{segment_id}', access_token))


def list_starred_segments(access_token: str, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List the authenticated athlete starred segments."""
    return _serialize_payload(
        _strava_request(
            'GET',
            '/segments/starred',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def set_segment_starred(access_token: str, segment_id: int, starred: bool) -> str:
    """Star or unstar a segment. Requires profile:write."""
    return _serialize_payload(
        _strava_request(
            'PUT',
            f'/segments/{segment_id}/starred',
            access_token,
            data={'starred': starred},
        )
    )


def list_segment_efforts(
    access_token: str,
    segment_id: int,
    start_date_local: str | None = None,
    end_date_local: str | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> str:
    """List efforts for a segment with optional date filters."""
    return _serialize_payload(
        _strava_request(
            'GET',
            '/segment_efforts',
            access_token,
            params={
                'segment_id': segment_id,
                'start_date_local': start_date_local,
                'end_date_local': end_date_local,
                'page': page,
                'per_page': per_page,
            },
        )
    )


def explore_segments(
    access_token: str,
    bounds: str,
    activity_type: str | None = None,
    min_cat: int | None = None,
    max_cat: int | None = None,
) -> str:
    """Explore segments inside bounds formatted as sw.lat,sw.lng,ne.lat,ne.lng."""
    return _serialize_payload(
        _strava_request(
            'GET',
            '/segments/explore',
            access_token,
            params={
                'bounds': bounds,
                'activity_type': activity_type,
                'min_cat': min_cat,
                'max_cat': max_cat,
            },
        )
    )


def get_segment_effort_by_id(access_token: str, effort_id: int) -> str:
    """Get a segment effort by id."""
    return _serialize_payload(_strava_request('GET', f'/segment_efforts/{effort_id}', access_token))


def create_activity(
    access_token: str,
    name: str,
    sport_type: str,
    start_date_local: str,
    elapsed_time: int,
    activity_type: str | None = None,
    description: str | None = None,
    distance_meters: float | None = None,
    trainer: int | None = None,
    commute: int | None = None,
) -> str:
    """Create a manual activity. Requires activity:write."""
    return _serialize_payload(
        _strava_request(
            'POST',
            '/activities',
            access_token,
            data={
                'name': name,
                'type': activity_type,
                'sport_type': sport_type,
                'start_date_local': start_date_local,
                'elapsed_time': elapsed_time,
                'description': description,
                'distance': distance_meters,
                'trainer': trainer,
                'commute': commute,
            },
        )
    )


def get_activity_by_id(access_token: str, activity_id: int, include_all_efforts: bool = False) -> str:
    """Get activity details by id."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/activities/{activity_id}',
            access_token,
            params={'include_all_efforts': include_all_efforts},
        )
    )


def update_activity_by_id(access_token: str, activity_id: int, body_json: str) -> str:
    """Update an activity using a JSON body string. Requires activity:write."""
    body = _parse_json_object_arg('body_json', body_json)
    return _serialize_payload(
        _strava_request('PUT', f'/activities/{activity_id}', access_token, json_body=body)
    )


def list_logged_in_athlete_activities(
    access_token: str,
    before: int | None = None,
    after: int | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> str:
    """List activities for the authenticated athlete."""
    return _serialize_payload(
        _strava_request(
            'GET',
            '/athlete/activities',
            access_token,
            params={'before': before, 'after': after, 'page': page, 'per_page': per_page},
        )
    )


def get_activity_laps(access_token: str, activity_id: int) -> str:
    """List laps for an activity."""
    return _serialize_payload(_strava_request('GET', f'/activities/{activity_id}/laps', access_token))


def get_activity_zones(access_token: str, activity_id: int) -> str:
    """Get zones for an activity."""
    return _serialize_payload(_strava_request('GET', f'/activities/{activity_id}/zones', access_token))


def get_activity_comments(
    access_token: str,
    activity_id: int,
    page: int | None = None,
    per_page: int | None = None,
    page_size: int | None = None,
    after_cursor: str | None = None,
) -> str:
    """List comments for an activity."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/activities/{activity_id}/comments',
            access_token,
            params={
                'page': page,
                'per_page': per_page,
                'page_size': page_size,
                'after_cursor': after_cursor,
            },
        )
    )


def get_activity_kudoers(access_token: str, activity_id: int, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List kudoers for an activity."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/activities/{activity_id}/kudos',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def get_club_by_id(access_token: str, club_id: int) -> str:
    """Get a club by id."""
    return _serialize_payload(_strava_request('GET', f'/clubs/{club_id}', access_token))


def get_club_members(access_token: str, club_id: int, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List members for a club."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/clubs/{club_id}/members',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def get_club_admins(access_token: str, club_id: int, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List administrators for a club."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/clubs/{club_id}/admins',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def get_club_activities(access_token: str, club_id: int, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List recent activities for a club."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/clubs/{club_id}/activities',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def list_logged_in_athlete_clubs(access_token: str, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List clubs for the authenticated athlete."""
    return _serialize_payload(
        _strava_request(
            'GET',
            '/athlete/clubs',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def get_gear_by_id(access_token: str, gear_id: str) -> str:
    """Get gear details by gear id."""
    return _serialize_payload(_strava_request('GET', f'/gear/{gear_id}', access_token))


def get_route_by_id(access_token: str, route_id: int) -> str:
    """Get a route by id."""
    return _serialize_payload(_strava_request('GET', f'/routes/{route_id}', access_token))


def list_athlete_routes(access_token: str, athlete_id: int, page: int = DEFAULT_PAGE, per_page: int = DEFAULT_PER_PAGE) -> str:
    """List routes created by an athlete."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/athletes/{athlete_id}/routes',
            access_token,
            params={'page': page, 'per_page': per_page},
        )
    )


def export_route_gpx(access_token: str, route_id: int) -> str:
    """Export a route as GPX text."""
    return _serialize_payload(
        _strava_request('GET', f'/routes/{route_id}/export_gpx', access_token, accept='application/gpx+xml')
    )


def export_route_tcx(access_token: str, route_id: int) -> str:
    """Export a route as TCX text."""
    return _serialize_payload(
        _strava_request('GET', f'/routes/{route_id}/export_tcx', access_token, accept='application/xml')
    )


def create_upload(
    access_token: str,
    file_path: str,
    data_type: str,
    name: str | None = None,
    description: str | None = None,
    trainer: str | None = None,
    commute: str | None = None,
    external_id: str | None = None,
) -> str:
    """Upload an activity file. Requires activity:write."""
    target = Path(file_path)
    if not target.exists() or not target.is_file():
        raise ValueError(f'file_path does not exist or is not a file: {file_path}')

    with target.open('rb') as file_handle:
        return _serialize_payload(
            _strava_request(
                'POST',
                '/uploads',
                access_token,
                data={
                    'name': name,
                    'description': description,
                    'trainer': trainer,
                    'commute': commute,
                    'data_type': data_type,
                    'external_id': external_id,
                },
                files={'file': (target.name, file_handle)},
            )
        )


def get_upload_by_id(access_token: str, upload_id: int) -> str:
    """Get upload status by id."""
    return _serialize_payload(_strava_request('GET', f'/uploads/{upload_id}', access_token))


def get_activity_streams(access_token: str, activity_id: int, keys: str, key_by_type: bool = True) -> str:
    """Get activity streams using comma-separated keys such as time,latlng,distance,heartrate,watts."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/activities/{activity_id}/streams',
            access_token,
            params={'keys': _parse_csv_arg(keys), 'key_by_type': key_by_type},
        )
    )


def get_segment_effort_streams(access_token: str, effort_id: int, keys: str, key_by_type: bool = True) -> str:
    """Get segment effort streams using comma-separated keys."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/segment_efforts/{effort_id}/streams',
            access_token,
            params={'keys': _parse_csv_arg(keys), 'key_by_type': key_by_type},
        )
    )


def get_segment_streams(access_token: str, segment_id: int, keys: str, key_by_type: bool = True) -> str:
    """Get segment streams using comma-separated keys."""
    return _serialize_payload(
        _strava_request(
            'GET',
            f'/segments/{segment_id}/streams',
            access_token,
            params={'keys': _parse_csv_arg(keys), 'key_by_type': key_by_type},
        )
    )


def get_route_streams(access_token: str, route_id: int) -> str:
    """Get route streams by route id."""
    return _serialize_payload(_strava_request('GET', f'/routes/{route_id}/streams', access_token))