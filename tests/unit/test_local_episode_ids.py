from utils.validation import is_valid_episode_id


def test_md5_ids_still_valid():
    assert is_valid_episode_id('0123456789ab')


def test_local_ids_valid():
    assert is_valid_episode_id('s01e05')
    assert is_valid_episode_id('s00e01')      # season 0 = specials
    assert is_valid_episode_id('s01e100')     # >99 episodes
    assert is_valid_episode_id('s100e01')     # >99 seasons


def test_invalid_ids_rejected():
    for bad in ('s1e5', 'S01E05', 's01e05 - title', 's01e', 'e05',
                's01e00001', '../s01e05', 's01e05.mp3', ''):
        assert not is_valid_episode_id(bad), bad
