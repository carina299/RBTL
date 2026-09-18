"""Tests for `AlbumRepository`/`AttachmentRepository` (extended with
the columns `web/album.html`'s actual API contract needs — see repositories.py's
`album_photo_dict` docstring) — against a real Postgres transaction.
"""

from datetime import datetime, timedelta, timezone

from repositories import AlbumRepository, AttachmentRepository, album_photo_dict


def _make_attachment(session, user_id, key="att-1.jpg"):
    return AttachmentRepository(session).create(user_id, key, "image/jpeg", 12345, width=800, height=600)


# --- AttachmentRepository ---------------------------------------------------

def test_attachment_create_and_get_many(db_session, test_user_id):
    repo = AttachmentRepository(db_session)
    a1 = repo.create(test_user_id, "att-1.jpg", "image/jpeg", 100, 800, 600)
    a2 = repo.create(test_user_id, "att-2.jpg", "image/jpeg", 200, 640, 480)

    found = repo.get_many([a2.id, a1.id])
    assert [a.id for a in found] == [a2.id, a1.id]  # preserves caller's order


def test_attachment_get_many_drops_missing_ids(db_session, test_user_id):
    repo = AttachmentRepository(db_session)
    a1 = repo.create(test_user_id, "att-1.jpg", "image/jpeg", 100)
    found = repo.get_many([a1.id, 999999])
    assert [a.id for a in found] == [a1.id]


def test_attachment_get_many_empty_input(db_session):
    assert AttachmentRepository(db_session).get_many([]) == []


# --- AlbumRepository: create / get ------------------------------------------

def test_create_and_get_entry(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    entry = repo.create(test_user_id, [att.id], caption="a day at the beach")

    fetched = repo.get(entry.id)
    assert fetched is not None
    assert fetched.caption == "a day at the beach"
    assert fetched.attachment_ids == [att.id]


def test_get_returns_none_for_missing_id(db_session):
    assert AlbumRepository(db_session).get(999999) is None


# --- AlbumRepository: timeline pagination -----------------------------------

def test_list_timeline_orders_by_taken_at_not_upload_order(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    now = datetime.now(timezone.utc)
    # uploaded in this order, but the "moment" each one represents is reversed
    old = repo.create(test_user_id, [att.id], caption="old photo", taken_at=now - timedelta(days=400))
    recent = repo.create(test_user_id, [att.id], caption="recent photo", taken_at=now - timedelta(days=1))

    page = repo.list_timeline(test_user_id, cursor=None, limit=10)
    ids = [e.id for e in page]
    assert ids.index(recent.id) < ids.index(old.id)


def test_list_timeline_falls_back_to_created_at_when_taken_at_is_null(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    entry = repo.create(test_user_id, [att.id], caption="no date given")  # taken_at=None
    page = repo.list_timeline(test_user_id, cursor=None, limit=10)
    assert entry.id in [e.id for e in page]


def test_list_timeline_pagination_via_next_cursor(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    now = datetime.now(timezone.utc)
    entries = [
        repo.create(test_user_id, [att.id], caption=f"entry {i}", taken_at=now - timedelta(days=i))
        for i in range(5)
    ]

    page1 = repo.list_timeline(test_user_id, cursor=None, limit=2)
    assert [e.id for e in page1] == [entries[0].id, entries[1].id]

    cursor = repo.next_cursor(page1)
    page2 = repo.list_timeline(test_user_id, cursor=cursor, limit=2)
    assert [e.id for e in page2] == [entries[2].id, entries[3].id]

    cursor2 = repo.next_cursor(page2)
    page3 = repo.list_timeline(test_user_id, cursor=cursor2, limit=2)
    assert [e.id for e in page3] == [entries[4].id]


def test_next_cursor_is_none_for_an_empty_page(db_session):
    assert AlbumRepository(db_session).next_cursor([]) is None


def test_list_timeline_only_returns_the_given_user(db_session, test_user_id):
    other_user_id = "22222222-2222-2222-2222-222222222222"
    att_mine = _make_attachment(db_session, test_user_id, "att-mine.jpg")
    att_theirs = _make_attachment(db_session, other_user_id, "att-theirs.jpg")
    repo = AlbumRepository(db_session)
    repo.create(test_user_id, [att_mine.id], caption="mine")
    repo.create(other_user_id, [att_theirs.id], caption="theirs")

    page = repo.list_timeline(test_user_id, cursor=None, limit=10)
    assert [e.caption for e in page] == ["mine"]


# --- group_name / time_label / views / notes (web/album.html's contract) ---

def test_create_stores_group_and_time_label(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    entry = repo.create(test_user_id, [att.id], group_name="Trips", time_label="2026-06-25")
    fetched = repo.get(entry.id)
    assert fetched.group_name == "Trips"
    assert fetched.time_label == "2026-06-25"


def test_new_entry_has_zero_views_and_no_notes(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    entry = AlbumRepository(db_session).create(test_user_id, [att.id])
    assert entry.views == 0
    assert entry.notes == []


def test_increment_views_bumps_by_one_each_call(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    entry = repo.create(test_user_id, [att.id])
    repo.increment_views(entry.id)
    repo.increment_views(entry.id)
    db_session.refresh(entry)
    assert entry.views == 2


def test_random_entry_returns_one_of_the_users_entries(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    repo = AlbumRepository(db_session)
    ids = {repo.create(test_user_id, [att.id], caption=f"photo {i}").id for i in range(5)}
    picked = repo.random_entry(test_user_id)
    assert picked is not None
    assert picked.id in ids


def test_random_entry_none_when_user_has_no_entries(db_session, test_user_id):
    assert AlbumRepository(db_session).random_entry(test_user_id) is None


# --- album_photo_dict (pure formatter) --------------------------------------

def test_album_photo_dict_shape(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    entry = AlbumRepository(db_session).create(
        test_user_id, [att.id], caption="a day out", group_name="Everyday", time_label="2026-05-01"
    )
    d = album_photo_dict(entry, "https://example.com/photo.jpg")
    assert d == {
        "id": entry.id,
        "img": "https://example.com/photo.jpg",
        "caption": "a day out",
        "time": "2026-05-01",
        "group": "Everyday",
        "views": 0,
    }


def test_album_photo_dict_passes_through_none_img_url(db_session, test_user_id):
    att = _make_attachment(db_session, test_user_id)
    entry = AlbumRepository(db_session).create(test_user_id, [att.id])
    assert album_photo_dict(entry, None)["img"] is None
