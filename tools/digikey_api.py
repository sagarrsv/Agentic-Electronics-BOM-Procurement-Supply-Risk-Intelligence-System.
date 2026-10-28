"""Minimal DigiKey Product Information v4 client (OAuth client credentials)."""
import os
import time
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv('DIGIKEY_API_BASE', 'https://api.digikey.com').rstrip('/')


class DigiKeyAPIError(RuntimeError):
    pass


class DigiKeyClient:
    def __init__(self):
        self.client_id = os.getenv('DIGIKEY_CLIENT_ID')
        self.client_secret = os.getenv('DIGIKEY_CLIENT_SECRET')
        if not self.client_id or not self.client_secret:
            raise DigiKeyAPIError('Set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET in .env')
        self.token = None
        self.expires_at = 0
        self.requests_made = 0

    def _access_token(self):
        """Request one OAuth token and reuse it until shortly before expiry."""
        if self.token and time.monotonic() < self.expires_at:
            return self.token
        r = requests.post(f'{BASE}/v1/oauth2/token', data={
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials',
        }, timeout=20)
        if not r.ok:
            raise DigiKeyAPIError(f'OAuth HTTP {r.status_code}: {r.text[:250]}')
        payload = r.json()
        self.token = payload['access_token']
        self.expires_at = time.monotonic() + max(0, int(payload.get('expires_in', 600)) - 45)
        return self.token

    def _request(self, method, url, **kwargs):
        """Retry temporary DigiKey outages; count every HTTP attempt."""
        for attempt in range(3):
            self.requests_made += 1
            response = requests.request(method, url, timeout=25, **kwargs)
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                return response
            time.sleep(min(4, 2 ** attempt))

    def product_details(self, mpn):
        """Read expanded product data for one MPN; do not search for substitutes."""
        headers = {
            'Authorization': f'Bearer {self._access_token()}',
            'X-DIGIKEY-Client-Id': self.client_id,
            'X-DIGIKEY-Locale-Site': os.getenv('DIGIKEY_SITE', 'US'),
            'X-DIGIKEY-Locale-Language': 'en',
            'X-DIGIKEY-Locale-Currency': os.getenv('DIGIKEY_CURRENCY', 'USD'),
            'Accept': 'application/json',
        }
        url = f'{BASE}/products/v4/search/{quote(mpn, safe="")}/productdetails'
        r = self._request('GET', url, headers=headers)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise DigiKeyAPIError(f'ProductDetails HTTP {r.status_code}: {r.text[:250]}')
        data = r.json()
        return data.get('Product', data)


    def keyword_search(self, mpn, limit=50):
        """Find multiple DigiKey catalog candidates instead of trusting a single hit."""
        headers = {
            'Authorization': f'Bearer {self._access_token()}',
            'X-DIGIKEY-Client-Id': self.client_id,
            'X-DIGIKEY-Locale-Site': os.getenv('DIGIKEY_SITE', 'US'),
            'X-DIGIKEY-Locale-Language': 'en',
            'X-DIGIKEY-Locale-Currency': os.getenv('DIGIKEY_CURRENCY', 'USD'),
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        r = self._request('POST', f'{BASE}/products/v4/search/keyword', headers=headers,
                          json={'Keywords': mpn, 'Limit': limit, 'Offset': 0})
        if not r.ok:
            raise DigiKeyAPIError(f'KeywordSearch HTTP {r.status_code}: {r.text[:250]}')
        return r.json().get('Products') or []
