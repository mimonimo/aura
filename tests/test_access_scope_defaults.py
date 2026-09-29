from zzaimy.app.access_guard import search_scope
from zzaimy.app.access_policy import visible


def test_missing_department_does_not_grant_other_department_access():
    document = {"dept": "입학처", "access_level": "dept", "owner": "other"}
    assert not visible(document, dept=None, user="user", role="staff")
    assert not visible(document, dept="학생처", user="user", role="staff")
    assert visible(document, dept="입학처", user="user", role="staff")
    assert search_scope(None, "staff", "user")["dept"] == "공통"


def test_missing_document_is_never_public():
    assert not visible({}, dept="학생처", user="user", role="staff")
