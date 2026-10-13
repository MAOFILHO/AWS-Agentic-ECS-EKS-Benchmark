"""aws login profiles need awscrt (botocore[crt]); make bootstrap fails without it."""

import awscrt


def test_awscrt_is_importable():
    assert awscrt.__name__ == "awscrt"
