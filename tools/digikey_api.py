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

    def manufacturers(self):
        """Read the official DigiKey v4 manufacturer directory (not product inventory)."""
        headers = {
            'Authorization': f'Bearer {self._access_token()}',
            'X-DIGIKEY-Client-Id': self.client_id,
            'X-DIGIKEY-Locale-Site': os.getenv('DIGIKEY_SITE', 'US'),
            'X-DIGIKEY-Locale-Language': 'en',
            'X-DIGIKEY-Locale-Currency': os.getenv('DIGIKEY_CURRENCY', 'USD'),
            'Accept': 'application/json',
        }
        r = self._request('GET', f'{BASE}/products/v4/search/manufacturers', headers=headers)
        if not r.ok:
            raise DigiKeyAPIError(f'Manufacturers HTTP {r.status_code}: {r.text[:250]}')
        data = r.json()
        entries = data.get('Manufacturers') if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise DigiKeyAPIError('Unexpected DigiKey manufacturer directory format')
        return [{'id': str(x.get('Id')), 'name': str(x.get('Name') or x.get('Value') or '').strip()}
                for x in entries if isinstance(x, dict) and x.get('Id') is not None
                and (x.get('Name') or x.get('Value'))]

    def product_details(self, mpn, manufacturer_id=None):
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
        r = self._request('GET', url, headers=headers,
                          params={'manufacturerId': str(manufacturer_id)} if manufacturer_id is not None else None)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise DigiKeyAPIError(f'ProductDetails HTTP {r.status_code}: {r.text[:250]}')
        data = r.json()
        return data.get('Product', data)


    def keyword_search(self, mpn, limit=50, manufacturer_id=None):
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
                          json={**{'Keywords': mpn, 'Limit': limit, 'Offset': 0},
                                **({'FilterOptionsRequest': {'ManufacturerFilter': [{'Id': str(manufacturer_id)}]}}
                                   if manufacturer_id is not None else {})})
        if not r.ok:
            raise DigiKeyAPIError(f'KeywordSearch HTTP {r.status_code}: {r.text[:250]}')
        return r.json().get('Products') or []

    # Async Product Information V4 calls are used only for per-MPN sourcing.
    # Manufacturer directory initialization remains synchronous and happens first.
    async def _async_access_token(self, client):
        import asyncio
        if self.token and time.monotonic() < self.expires_at:
            return self.token
        if not hasattr(self, '_async_token_lock'):
            self._async_token_lock = asyncio.Lock()
        async with self._async_token_lock:
            if self.token and time.monotonic() < self.expires_at:
                return self.token
            r = await client.post(f'{BASE}/v1/oauth2/token', data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials',
            }, timeout=20)
            if not r.is_success:
                raise DigiKeyAPIError(f'OAuth HTTP {r.status_code}: {r.text[:250]}')
            payload = r.json()
            self.token = payload['access_token']
            self.expires_at = time.monotonic() + max(0, int(payload.get('expires_in', 600)) - 45)
            return self.token

    async def _async_request(self, client, method, url, **kwargs):
        import asyncio
        for attempt in range(3):
            # Space start times of HTTP attempts across concurrent MPN tasks.
            # This is a cautious configurable throttle, not a substitute for
            # checking the actual supplier quota for the application key.
            interval = float(os.getenv('DIGIKEY_MIN_REQUEST_INTERVAL_SECONDS', '0.25'))
            async with self._async_request_lock:
                pause = max(0, self._async_next_request_at - time.monotonic())
                if pause:
                    await asyncio.sleep(pause)
                self._async_next_request_at = time.monotonic() + max(0, interval)
                self.requests_made += 1
            response = await client.request(method, url, timeout=25, **kwargs)
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                return response
            retry_after = response.headers.get('Retry-After')
            try:
                pause = min(10.0, max(0.0, float(retry_after))) if retry_after else min(4, 2 ** attempt)
            except ValueError:
                pause = min(4, 2 ** attempt)
            await asyncio.sleep(pause)

    async def _async_headers(self, client, json_body=False):
        h = {
            'Authorization': f'Bearer {await self._async_access_token(client)}',
            'X-DIGIKEY-Client-Id': self.client_id,
            'X-DIGIKEY-Locale-Site': os.getenv('DIGIKEY_SITE', 'US'),
            'X-DIGIKEY-Locale-Language': 'en',
            'X-DIGIKEY-Locale-Currency': os.getenv('DIGIKEY_CURRENCY', 'USD'),
            'Accept': 'application/json',
        }
        if json_body:
            h['Content-Type'] = 'application/json'
        return h

    async def async_product_details(self, client, mpn, manufacturer_id=None):
        headers = await self._async_headers(client)
        url = f'{BASE}/products/v4/search/{quote(mpn, safe="")}/productdetails'
        r = await self._async_request(client, 'GET', url, headers=headers,
                                      params={'manufacturerId': str(manufacturer_id)} if manufacturer_id is not None else None)
        if r.status_code == 404:
            return None
        if not r.is_success:
            raise DigiKeyAPIError(f'ProductDetails HTTP {r.status_code}: {r.text[:250]}')
        data = r.json()
        return data.get('Product', data)

    async def async_keyword_search(self, client, mpn, limit=50, manufacturer_id=None):
        headers = await self._async_headers(client, json_body=True)
        payload = {'Keywords': mpn, 'Limit': limit, 'Offset': 0}
        if manufacturer_id is not None:
            payload['FilterOptionsRequest'] = {'ManufacturerFilter': [{'Id': str(manufacturer_id)}]}
        r = await self._async_request(client, 'POST', f'{BASE}/products/v4/search/keyword',
                                      headers=headers, json=payload)
        if not r.is_success:
            raise DigiKeyAPIError(f'KeywordSearch HTTP {r.status_code}: {r.text[:250]}')
        return r.json().get('Products') or []
