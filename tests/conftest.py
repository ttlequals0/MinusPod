"""Shared pytest fixtures for podcast server tests."""
import os
import sys
import tempfile
import shutil
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import Database


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    tmpdir = tempfile.mkdtemp(prefix='podcast_test_')
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_db(temp_dir):
    """Create a temporary database for testing.

    IMPORTANT: Database uses singleton pattern - we must reset _instance
    to get a fresh database for each test.
    """
    # Reset singleton to ensure fresh database
    Database._instance = None

    db = Database(data_dir=temp_dir)
    yield db

    # Reset singleton after test
    Database._instance = None


@pytest.fixture
def sample_transcript():
    """Sample transcript with ad segments for testing."""
    return [
        {'start': 0.0, 'end': 10.0, 'text': 'Welcome to the podcast.'},
        {'start': 10.0, 'end': 20.0, 'text': 'Today we have a great episode.'},
        {'start': 20.0, 'end': 30.0, 'text': 'But first, a word from our sponsors.'},
        {'start': 30.0, 'end': 45.0, 'text': 'This episode is brought to you by BetterHelp.'},
        {'start': 45.0, 'end': 60.0, 'text': 'Visit betterhelp.com/podcast for 10 percent off.'},
        {'start': 60.0, 'end': 75.0, 'text': 'BetterHelp is online therapy that fits your schedule.'},
        {'start': 75.0, 'end': 90.0, 'text': 'That is betterhelp.com/podcast.'},
        {'start': 90.0, 'end': 100.0, 'text': 'Alright, back to the show.'},
        {'start': 100.0, 'end': 200.0, 'text': 'So as I was saying about the topic...'},
        {'start': 200.0, 'end': 300.0, 'text': 'More content here about the episode topic.'},
    ]


@pytest.fixture
def sample_ads():
    """Sample ad markers for validation testing."""
    return [
        {
            'start': 30.0,
            'end': 90.0,
            'confidence': 0.95,
            'reason': 'BetterHelp sponsor read with promo code',
            'end_text': 'betterhelp.com/podcast'
        }
    ]


@pytest.fixture
def low_confidence_ad():
    """Ad with low confidence for rejection testing."""
    return {
        'start': 100.0,
        'end': 120.0,
        'confidence': 0.25,
        'reason': 'Possible ad detected'
    }


@pytest.fixture
def short_ad():
    """Ad that is too short to be valid."""
    return {
        'start': 50.0,
        'end': 55.0,
        'confidence': 0.80,
        'reason': 'Quick mention'
    }


@pytest.fixture
def long_ad():
    """Ad that exceeds normal duration limits."""
    return {
        'start': 100.0,
        'end': 450.0,
        'confidence': 0.70,
        'reason': 'Extended promotional segment'
    }


@pytest.fixture
def overlapping_ads():
    """Ads that overlap and should be merged."""
    return [
        {'start': 30.0, 'end': 60.0, 'confidence': 0.90, 'reason': 'First pass ad'},
        {'start': 55.0, 'end': 90.0, 'confidence': 0.85, 'reason': 'Second pass ad'}
    ]


@pytest.fixture
def adjacent_ads():
    """Ads with small gaps that should be merged."""
    return [
        {'start': 30.0, 'end': 60.0, 'confidence': 0.90, 'reason': 'First ad'},
        {'start': 63.0, 'end': 90.0, 'confidence': 0.85, 'reason': 'Second ad within gap'}
    ]


@pytest.fixture
def mock_podcast(temp_db):
    """Create a test podcast in the database."""
    slug = 'test-podcast'
    source_url = 'https://example.com/feed.xml'
    title = 'Test Podcast'

    podcast_id = temp_db.create_podcast(slug, source_url, title)
    podcast = temp_db.get_podcast_by_slug(slug)

    return podcast


@pytest.fixture
def mock_episode(temp_db, mock_podcast):
    """Create a test episode in the database."""
    slug = mock_podcast['slug']
    episode_id = 'test-episode-001'

    temp_db.upsert_episode(
        slug,
        episode_id,
        original_url='https://example.com/episode.mp3',
        title='Test Episode',
        status='pending'
    )

    episode = temp_db.get_episode(slug, episode_id)
    return episode


@pytest.fixture
def app_client():
    """Flask test client for API integration tests."""
    # Import here to avoid circular imports and allow test isolation
    from main import app

    app.config['TESTING'] = True
    app.config['DEBUG'] = False

    with app.test_client() as client:
        yield client
