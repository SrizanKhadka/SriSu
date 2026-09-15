# Couple Moments: Postman test cases

## Setup and execution order

1. Import `Couple_Moments.postman_collection.json` into Postman. It includes request bodies, role-specific Bearer authentication, expected status assertions, and automatic ID capture.
2. Run the migrated backend in your development/test environment. Set collection variable `base_url` to its origin, for example `http://127.0.0.1:8000` (no trailing slash).
3. Set **collection variables** `creator_token`, `partner_token`, `sender_token`, and `stranger_token` to JWT access tokens, without the `Bearer ` prefix. Creator and partner must be the two active members of one accepted couple. Sender and stranger must be distinct users outside that couple; nobody in these fixtures should be blocked. No real tokens are stored in the collection.
4. Run folders **01 through 04** in order. Folder 01 saves IDs automatically. Do not define environment variables with the same ID names: environment values would override captured collection values.
5. In **05**, choose the required local files for each File row before sending. Use Body > form-data; repeated `photos` keys each contain one file. Do not manually set `Content-Type`, because Postman must generate the multipart boundary. Special invalid-file tests explain exactly which file to select.
6. Run **06 last** to delete the test records. Skip photo-moment deletion if you skipped folder 05. Folder **07** needs separate expired fixtures and is manual-only. For repeated runs, recreate fixtures and remember that failed note submissions also count toward the 30/hour throttle.

GET and DELETE requests have **no body**. JSON requests use Body > raw > JSON. Multipart ID arrays are text such as `[81]`; JSON arrays use numeric IDs such as `{"deleted_photo_ids":[81]}`. Successful GET moment detail is an unwrapped object; create/update responses wrap it in `data`. List responses use `data.count`, `next`, `previous`, and `results`.

## Getting access tokens

Use your existing login flow. If needed, send the following requests manually for each test account (sending OTP can send an SMS):

```http
POST {{base_url}}/api/auth/send-otp/
Content-Type: application/json

{"phone_number":"+9779800000001"}
```

```http
POST {{base_url}}/api/auth/verify-otp/
Content-Type: application/json

{"phone_number":"+9779800000001","otp_code":"123456"}
```

Replace the phone and OTP with the actual test account and received code. Copy `data.tokens.access` from successful verification into the corresponding token variable. An OTP is not an access token. Authentication calls are not part of the runnable collection, so running it cannot unexpectedly send SMS.

## Manual scenarios requiring prepared state

**Expiry:** create a separate public moment with a photo as creator and a note as sender. Copy their IDs to `expired_moment_id`, `expired_photo_id`, and `retained_note_id`. Wait until the returned `expires_at`, or use an isolated test database/admin fixture to set the moment's creation and expiry into the past while preserving their 24-hour difference. Client-submitted expiry is ignored. Then run folder 07. Both partners must still be active/current, and the record must not have been permanently purged. Exact microsecond boundary testing and concurrent database/storage failures belong in `social/test_moments.py`; Postman's sequential requests do not prove transaction locking.

**Pagination:** folder 01 creates three visible moments. Use `page_size=2` and follow the actual `data.next` URL; page two should contain the remaining record in a clean test couple. A missing page returns 404. Newer `created_at` sorts first, with higher ID breaking equal-time ties. Existing records can change expected counts, so do not assume a globally empty feed.

**Blocking:** with a fresh public moment/note, prepare a BLOCKED single-connection record between sender and either partner. Sender's GET moment, GET photo, POST note and GET note return 404; blocked recipients cannot read the sender's note. Restore state before other tests.

**Membership:** after preparing a moment/note, end the accepted couple connection. Moment GET returns 404 for everyone, and recipient GET note returns 404; sender GET note remains 200 unless blocked. A replacement partner never gains access to the earlier private notes. Use a replacement token to verify both GET note = 404 and note list excludes it. Restore fixtures before other tests.

**Throttling:** on a fresh unexpired public moment, send `POST /api/social/couple-moments/{id}/notes/` as the same outside sender with `{"message":"Rate-limit test"}` repeatedly. With an empty throttle cache, attempts 1-30 return 201 and attempt 31 returns 429. Earlier successful/invalid attempts reduce the allowance. The test creates notes; delete them afterward. This scenario is deliberately outside the normal runner.

**Storage/DB failure and concurrent updates:** use the automated Django tests, which inject failures and synchronize real PostgreSQL transactions. Requests in folder 05 verify observable rollback for photo-limit rejection but cannot simulate an actual storage outage on their own.

## Complete request cases

Each case below corresponds to the identically numbered request in the collection. All paths use `{{base_url}}/api/social/` unless shown otherwise. Replace/capture variables before sending. Status assertions are included in Postman's Tests/Post-response scripts.


## 01 - Create fixtures

Run these first. Creator and partner must be active members of the same accepted couple. IDs are saved automatically.

### 01 Get my couple profile

`GET {{base_url}}/api/social/couple-profile/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 02 Creator creates a public moment

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **201**.

Expected HTTP 201. Authenticate as creator.

```json
{
  "title": "Postman cafe test",
  "caption": "Coffee and a rainy afternoon",
  "moment_date": "2026-09-14",
  "mood": "happy",
  "location_name": "Kathmandu",
  "visibility": "public",
  "tags": [
    "coffee",
    "travel"
  ],
  "partner_memory": "A lovely afternoon"
}
```

### 03 Creator creates a private moment

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **201**.

Expected HTTP 201. Authenticate as creator.

```json
{
  "caption": "Only our couple can see this"
}
```

### 04 Other partner also creates a moment

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{partner_token}}`. Expected: **201**.

Expected HTTP 201. Authenticate as partner.

```json
{
  "couple": "{{couple_id}}",
  "caption": "Created by the other partner",
  "visibility": "public"
}
```

## 02 - Read and update moments



### 05 List visible moments

`GET {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 06 Filter by couple and paginate

`GET {{base_url}}/api/social/couple-moments/?couple={{couple_id}}&page=1&page_size=2`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 07 Get creator moment

`GET {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 08 Partner reads private moment

`GET {{base_url}}/api/social/couple-moments/{{private_moment_id}}/`

Auth: `{{partner_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as partner.

Body: **none**.

### 09 Outside user reads public moment

`GET {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{sender_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as sender.

Body: **none**.

### 10 PATCH caption and mood

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

```json
{
  "caption": "Updated cafe caption",
  "mood": "grateful"
}
```

### 11 PUT moment metadata

`PUT {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

```json
{
  "couple": "{{couple_id}}",
  "title": "Our cafe visit",
  "caption": "A complete metadata update",
  "moment_date": "2026-09-14",
  "mood": "happy",
  "location_name": "Patan",
  "visibility": "public",
  "tags": [
    "cafe"
  ],
  "partner_memory": "Wonderful coffee"
}
```

### 12 Ownership and expiry spoofing are ignored

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

```json
{
  "created_by": "{{partner_user_id}}",
  "expires_at": "2099-01-01T00:00:00Z",
  "caption": "Ownership remains unchanged"
}
```

## 03 - Moment permission and validation failures



### 13 No authentication

`GET {{base_url}}/api/social/couple-moments/`

Auth: `No authentication`. Expected: **401**.

Expected HTTP 401. Authenticate as none.

Body: **none**.

### 14 Outsider cannot read private moment

`GET {{base_url}}/api/social/couple-moments/{{private_moment_id}}/`

Auth: `{{sender_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as sender.

Body: **none**.

### 15 Partner cannot PATCH creator moment

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{partner_token}}`. Expected: **403**.

Expected HTTP 403. Authenticate as partner.

```json
{
  "caption": "Not allowed"
}
```

### 16 Partner cannot PUT creator moment

`PUT {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{partner_token}}`. Expected: **403**.

Expected HTTP 403. Authenticate as partner.

```json
{
  "caption": "Not allowed"
}
```

### 17 Partner cannot DELETE creator moment

`DELETE {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{partner_token}}`. Expected: **403**.

Expected HTTP 403. Authenticate as partner.

Body: **none**.

### 18 Outside user cannot create for this couple

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{sender_token}}`. Expected: **403**.

Expected HTTP 403. Authenticate as sender.

```json
{
  "couple": "{{couple_id}}",
  "caption": "Not my couple"
}
```

### 19 Empty creation

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{}
```

### 20 Blank caption without photos

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "caption": "   "
}
```

### 21 Caption longer than 1000 characters

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "caption": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

### 22 Friends visibility is unsupported

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "caption": "Test",
  "visibility": "friends"
}
```

### 23 Invalid mood

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "caption": "Test",
  "mood": "angry"
}
```

### 24 Invalid couple filter

`GET {{base_url}}/api/social/couple-moments/?couple=abc`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

Body: **none**.

### 25 Invalid moment ID

`GET {{base_url}}/api/social/couple-moments/abc/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

Body: **none**.

### 26 Cannot move moment to another couple

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "couple": 9223372036854775807
}
```

### 27 A URL is not an uploaded image

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "caption": "Test",
  "photos": [
    "https://example.com/image.jpg"
  ]
}
```

### 28 Unknown photo deletion reference

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "deleted_photo_ids": [
    9223372036854775807
  ]
}
```

### 29 Invalid deletion array type

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "deleted_photo_ids": "not-json"
}
```

## 04 - Private appreciation notes

Run once before deletion tests. Repeated runs/invalid attempts count toward the 30/hour sender throttle.

### 30 Outside user sends appreciation

`POST {{base_url}}/api/social/couple-moments/{{public_moment_id}}/notes/`

Auth: `{{sender_token}}`. Expected: **201**.

Expected HTTP 201. Authenticate as sender.

```json
{
  "message": "You two look so happy. Wishing you more afternoons like this!"
}
```

### 31 Sender lists notes for the moment

`GET {{base_url}}/api/social/moment-notes/?moment={{public_moment_id}}&page_size=10`

Auth: `{{sender_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as sender.

Body: **none**.

### 32 Creator reads private note

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 33 Partner reads private note

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{partner_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as partner.

Body: **none**.

### 34 Sender reads their own note

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{sender_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as sender.

Body: **none**.

### 35 Unrelated user cannot read note

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{stranger_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as stranger.

Body: **none**.

### 36 Unrelated user sees no notes for this moment

`GET {{base_url}}/api/social/moment-notes/?moment={{public_moment_id}}`

Auth: `{{stranger_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as stranger.

Body: **none**.

### 37 Blank appreciation is rejected

`POST {{base_url}}/api/social/couple-moments/{{public_moment_id}}/notes/`

Auth: `{{sender_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as sender.

```json
{
  "message": "   "
}
```

### 38 Appreciation longer than 1000 characters

`POST {{base_url}}/api/social/couple-moments/{{public_moment_id}}/notes/`

Auth: `{{sender_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as sender.

```json
{
  "message": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

### 39 Cannot appreciate your own couple

`POST {{base_url}}/api/social/couple-moments/{{public_moment_id}}/notes/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

```json
{
  "message": "Self note"
}
```

### 40 Cannot send appreciation to hidden moment

`POST {{base_url}}/api/social/couple-moments/{{private_moment_id}}/notes/`

Auth: `{{sender_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as sender.

```json
{
  "message": "Hidden"
}
```

### 41 Notes cannot be edited

`PATCH {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{sender_token}}`. Expected: **405**.

Expected HTTP 405. Authenticate as sender.

```json
{
  "message": "Changed"
}
```

### 42 Recipient cannot delete sender note

`DELETE {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{partner_token}}`. Expected: **403**.

Expected HTTP 403. Authenticate as partner.

Body: **none**.

### 43 Unrelated user cannot delete note

`DELETE {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{stranger_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as stranger.

Body: **none**.

### 44 Invalid notes moment filter

`GET {{base_url}}/api/social/moment-notes/?moment=abc`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

Body: **none**.

## 05 - Photo uploads and updates

Before running, select files for EVERY file row. Each row expects one file. Postman sets multipart Content-Type; do not set it manually. Run in order.

### 45 Create moment with two photos

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **201**.

Expected HTTP 201. Authenticate as creator.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `caption` | text | Two photos from our day |
| `visibility` | text | private |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |

### 46 Creator downloads photo

`GET {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/photos/{{photo_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 47 Partner downloads private photo

`GET {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/photos/{{photo_id}}/`

Auth: `{{partner_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as partner.

Body: **none**.

### 48 Outsider cannot download private photo

`GET {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/photos/{{photo_id}}/`

Auth: `{{sender_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as sender.

Body: **none**.

### 49 Append three photos - total five

`PATCH {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |

### 50 Sixth photo is rejected on update

`PATCH {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `caption` | text | This must not be saved |
| `photos` | file | Select one local file |

### 51 Verify rejected update did not change moment

`GET {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 52 Reject another moments photo ID

`PATCH {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **400**.

This photo belongs to upload_moment_id, not public_moment_id. The numeric photo ID must be rejected with 400.

```json
{"deleted_photo_ids": [{{photo_id}}]}
```

### 53 Remove one photo and append one - still five

`PATCH {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `deleted_photo_ids` | text | [{{photo_id}}] |
| `photos` | file | Select one local file |

### 54 Removed photo is inaccessible

`GET {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/photos/{{removed_photo_id}}/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

Body: **none**.

### 55 Replace all photos with two new ones

`PATCH {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `replace_photos` | text | true |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |

### 56 Delete photo through JSON update

`PATCH {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

```json
{"deleted_photo_ids": [{{photo_id}}]}
```

### 57 Six photos are rejected on creation

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Expected HTTP 400. Authenticate as creator.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `caption` | text | Too many |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |
| `photos` | file | Select one local file |

### 58 Invalid image bytes

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Select a plain text file renamed fake.png. Expected 400; filename/content-type alone is not trusted.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `caption` | text | Bad image |
| `photos` | file | Select one local file |

### 59 Unsupported GIF image

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Select a real GIF file. JPEG, PNG and WebP are the supported formats. Expected 400.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `caption` | text | Unsupported |
| `photos` | file | Select one local file |

### 60 Oversized image

`POST {{base_url}}/api/social/couple-moments/`

Auth: `{{creator_token}}`. Expected: **400**.

Select a valid image larger than MOMENT_MAX_IMAGE_BYTES (default 10 MiB). Expected Django 400; a smaller reverse-proxy request limit may return 413 before Django.

Body > **form-data**:

| Key | Type | Value |
|---|---|---|
| `caption` | text | Too large |
| `photos` | file | Select one local file |

## 06 - Permanent deletion - run last

These requests delete only moments/notes whose IDs were captured by this collection. Run conditional expiry/membership tests before this folder if desired.

### 61 Sender deletes their note

`DELETE {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{sender_token}}`. Expected: **204**.

Expected HTTP 204. Authenticate as sender.

Body: **none**.

### 62 Deleted note no longer exists

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/`

Auth: `{{sender_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as sender.

Body: **none**.

### 63 Create note to verify cascade deletion

`POST {{base_url}}/api/social/couple-moments/{{public_moment_id}}/notes/`

Auth: `{{sender_token}}`. Expected: **201**.

Expected HTTP 201. Authenticate as sender.

```json
{
  "message": "This note belongs to the soon-deleted moment"
}
```

### 64 Creator permanently deletes public moment

`DELETE {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 65 Deleted moment no longer exists

`GET {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

Body: **none**.

### 66 Notes are deleted with their moment

`GET {{base_url}}/api/social/moment-notes/{{cascade_note_id}}/`

Auth: `{{sender_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as sender.

Body: **none**.

### 67 Creator deletes photo moment

`DELETE {{base_url}}/api/social/couple-moments/{{upload_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 68 Creator deletes private moment

`DELETE {{base_url}}/api/social/couple-moments/{{private_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

### 69 Partner deletes their own moment

`DELETE {{base_url}}/api/social/couple-moments/{{partner_moment_id}}/`

Auth: `{{partner_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as partner.

Body: **none**.

## 07 - Conditional expiry tests - manual only

Do not include this folder in an ordinary collection run. Requires expired_moment_id, expired_photo_id and retained_note_id prepared as described in the guide. Use an expired PUBLIC moment authored by creator, with a note authored by sender; expiry must be less than 30 days ago.

### 70 Expired detail is hidden

`GET {{base_url}}/api/social/couple-moments/{{expired_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

Body: **none**.

### 71 Expired photo is hidden

`GET {{base_url}}/api/social/couple-moments/{{expired_moment_id}}/photos/{{expired_photo_id}}/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

Body: **none**.

### 72 Expired moment cannot be updated

`PATCH {{base_url}}/api/social/couple-moments/{{expired_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

```json
{
  "caption": "Cannot revive"
}
```

### 73 Expired moment cannot be deleted via ordinary API

`DELETE {{base_url}}/api/social/couple-moments/{{expired_moment_id}}/`

Auth: `{{creator_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as creator.

Body: **none**.

### 74 Cannot send note after expiry

`POST {{base_url}}/api/social/couple-moments/{{expired_moment_id}}/notes/`

Auth: `{{sender_token}}`. Expected: **404**.

Expected HTTP 404. Authenticate as sender.

```json
{
  "message": "Too late"
}
```

### 75 Sender retains access to earlier note

`GET {{base_url}}/api/social/moment-notes/{{retained_note_id}}/`

Auth: `{{sender_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as sender.

Body: **none**.

### 76 Original partner retains access to earlier note

`GET {{base_url}}/api/social/moment-notes/{{retained_note_id}}/`

Auth: `{{partner_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as partner.

Body: **none**.

### 77 Expired moment excluded from couple feed

`GET {{base_url}}/api/social/couple-moments/?couple={{couple_id}}&page_size=100`

Auth: `{{creator_token}}`. Expected: **200**.

Expected HTTP 200. Authenticate as creator.

Body: **none**.

## Partner replies and view-count tests (78-89)

The collection now contains 89 cases. These 12 new cases are placed at the **end of folder 04**, after case 44 and before photo/deletion folders. Run them once per fresh note: cases asserting two replies assume cases 78 and 79 were each sent exactly once.

Both original current partners can reply, regardless of moment creator. Only GET moment responses for those members embed `appreciation_notes`; outsiders use the private notes endpoint for their own threads. `total_view_count` counts distinct authenticated detail viewers, including partners; repeated detail requests and feed/photo/HEAD requests do not inflate it. Existing view counts start at zero after migration 0009.

### 78 Moment creator replies to appreciation

`POST {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 201; authenticate as creator.

Body > raw > JSON:

```json
{
  "message": "Thank you for your kind words!"
}
```

### 79 Other partner also replies to appreciation

`POST {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 201; authenticate as partner.

Body > raw > JSON:

```json
{
  "message": "Thanks from me too!"
}
```

### 80 Note sender reads both replies

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 200; authenticate as sender.

Body: **none**.

### 81 Unrelated user cannot read replies

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 404; authenticate as stranger.

Body: **none**.

### 82 Outside note sender cannot reply as a partner

`POST {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 403; authenticate as sender.

Body > raw > JSON:

```json
{
  "message": "Not a couple member"
}
```

### 83 Blank reply rejected

`POST {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 400; authenticate as partner.

Body > raw > JSON:

```json
{
  "message": "   "
}
```

### 84 Couple member receives embedded appreciation threads

`GET {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Run after case 44 and before deletion. Expected 200; authenticate as partner.

Body: **none**.

### 85 Outside viewer receives count without private threads

`GET {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Run after case 44 and before deletion. Expected 200; authenticate as sender.

Body: **none**.

### 86 Repeated detail GET does not inflate view count

`GET {{base_url}}/api/social/couple-moments/{{public_moment_id}}/`

Run after case 44 and before deletion. Expected 200; authenticate as sender.

Body: **none**.

### 87 Feed includes count without recording another view

`GET {{base_url}}/api/social/couple-moments/?couple={{couple_id}}&page_size=100`

Run after case 44 and before deletion. Expected 200; authenticate as sender.

Body: **none**.

### 88 Note detail includes both partner replies

`GET {{base_url}}/api/social/moment-notes/{{note_id}}/`

Run after case 44 and before deletion. Expected 200; authenticate as sender.

Body: **none**.

### 89 Reply longer than 1000 characters is rejected

`POST {{base_url}}/api/social/moment-notes/{{note_id}}/replies/`

Run after case 44 and before deletion. Expected 400; authenticate as creator.

Body > raw > JSON:

```json
{
  "message": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

