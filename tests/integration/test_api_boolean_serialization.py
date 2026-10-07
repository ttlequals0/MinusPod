def test_pattern_sponsor_and_normalization_active_fields_are_booleans(app_client, temp_db):
    active_pattern_id = temp_db.create_ad_pattern(
        'global', text_template='active boolean serialization pattern'
    )
    inactive_pattern_id = temp_db.create_ad_pattern(
        'global', text_template='inactive boolean serialization pattern'
    )
    temp_db.update_ad_pattern(inactive_pattern_id, is_active=0)

    patterns = app_client.get('/api/v1/patterns?active_only=false').get_json()['patterns']
    pattern_states = {row['id']: row['is_active'] for row in patterns}
    assert pattern_states[active_pattern_id] is True
    assert pattern_states[inactive_pattern_id] is False
    assert app_client.get(f'/api/v1/patterns/{active_pattern_id}').get_json()['is_active'] is True
    assert app_client.get(f'/api/v1/patterns/{inactive_pattern_id}').get_json()['is_active'] is False

    exported = app_client.get('/api/v1/patterns/export?include_disabled=true').get_json()
    exported_states = {row['text_template']: row['is_active'] for row in exported['patterns']}
    assert exported_states['active boolean serialization pattern'] is True
    assert exported_states['inactive boolean serialization pattern'] is False

    active_sponsor_id = temp_db.create_known_sponsor('Active Boolean Sponsor')
    inactive_sponsor_id = temp_db.create_known_sponsor('Inactive Boolean Sponsor')
    temp_db.update_known_sponsor(inactive_sponsor_id, is_active=0)

    sponsors = app_client.get('/api/v1/sponsors?include_inactive=true').get_json()['sponsors']
    sponsor_states = {row['id']: row['is_active'] for row in sponsors}
    assert sponsor_states[active_sponsor_id] is True
    assert sponsor_states[inactive_sponsor_id] is False
    assert app_client.get(f'/api/v1/sponsors/{active_sponsor_id}').get_json()['is_active'] is True
    assert app_client.get(f'/api/v1/sponsors/{inactive_sponsor_id}').get_json()['is_active'] is False

    active_normalization_id = temp_db.create_sponsor_normalization(
        'active_terms', 'active canonical', 'phrase'
    )
    inactive_normalization_id = temp_db.create_sponsor_normalization(
        'inactive_terms', 'inactive canonical', 'phrase'
    )
    temp_db.update_sponsor_normalization(inactive_normalization_id, is_active=0)

    normalizations = app_client.get(
        '/api/v1/sponsors/normalizations?include_inactive=true'
    ).get_json()['normalizations']
    normalization_states = {row['id']: row['is_active'] for row in normalizations}
    assert normalization_states[active_normalization_id] is True
    assert normalization_states[inactive_normalization_id] is False
