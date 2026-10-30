"""
Thread-safe Google Sheets service builder.

googleapiclient's default setup (build(..., credentials=...)) shares a single
httplib2.Http connection across all threads. httplib2 is NOT thread-safe, and
Flask serves each request on its own thread — so when the frontend fires
parallel requests (the payroll page loads employees + attendance + payroll at
once), concurrent use corrupts the TLS stream and requests fail with
"ssl.SSLError: [SSL] record layer failure".

The fix is the pattern documented by google-api-python-client for
multithreading: share the credentials (token refresh is internally locked)
but give every API request its own fresh httplib2.Http transport.
"""
import httplib2
import google_auth_httplib2
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest


def build_sheets_service(credentials):
    def request_builder(http, *args, **kwargs):
        # Ignore the shared http; every request gets its own transport.
        new_http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http())
        return HttpRequest(new_http, *args, **kwargs)

    return build(
        'sheets', 'v4',
        requestBuilder=request_builder,
        http=google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http()),
    )
