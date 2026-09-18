import pytest
from app.config import settings

pytestmark = pytest.mark.no_db

def test_about_page_and_alias(client):
    response = client.get('/about')
    assert response.status_code == 200
    assert 'A simpler way to hear from your customers' in response.text
    assert 'Pre-launch policy draft' not in response.text
    assert 'index, follow' in response.text
    assert response.headers.get('x-robots-tag') is None
    assert client.get('/about-us', follow_redirects=False).headers['location'] == '/about'

def test_unconfirmed_contact_is_not_invented(client, monkeypatch):
    monkeypatch.setattr(settings, 'GRIEVANCE_CONTACT_NAME', '')
    response = client.get('/contact')
    assert 'awaiting confirmation' in response.text
    monkeypatch.setattr(settings, 'GRIEVANCE_CONTACT_NAME', 'Confirmed Contact')
    monkeypatch.setattr(settings, 'BUSINESS_ADDRESS', 'Confirmed operator address')
    response = client.get('/contact')
    assert 'Confirmed Contact' in response.text and 'Confirmed operator address' in response.text
    assert 'awaiting confirmation' not in response.text
