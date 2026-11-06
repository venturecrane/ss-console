"""Fixtures for the accepted-library tests: a Word package built in code, a fake
vault, and acceptances. Every name and every line of document text is made up."""

from __future__ import annotations

import hashlib
import io
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
FIXED = (
    "IN THE SUPERIOR COURT OF THE STATE OF EXAMPLE",
    "Plaintiff,",
    "Defendant.",
    "Respectfully submitted,",
)


def p(*runs: str) -> str:
    """A paragraph whose runs hold ``runs``, one text element each."""
    return (
        "<w:p>"
        + "".join(f'<w:r><w:t xml:space="preserve">{r}</w:t></w:r>' for r in runs)
        + "</w:p>"
    )


FORM_BODY = (
    p("IN THE SUPERIOR COURT ", "OF THE STATE OF EXAMPLE")
    + p("[[party.plaintiff]]")
    + p("Plaintiff,")
    + p("[[", "party.defendant", "]]")
    + p("Defendant.")
    + p("[[body.argument]]")
    + p("Respectfully submitted,")
    + p("{{signer.name}}, ", "[[signer.bar]]")
)

FOOTER = (
    '<w:p><w:r><w:t xml:space="preserve">Page </w:t></w:r>'
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>'
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    '<w:r><w:t xml:space="preserve"> of </w:t></w:r><w:r><w:t>2</w:t></w:r></w:p>'
)

_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:creator>{creator}</dc:creator><cp:lastModifiedBy></cp:lastModifiedBy><dc:title></dc:title>"
    "</cp:coreProperties>"
)


def docx(
    body: str = FORM_BODY,
    *,
    footer: str | None = FOOTER,
    creator: str = "",
    main_type: str = DOCX_MAIN,
    extra_parts: dict[str, str] | None = None,
) -> bytes:
    """A Word package with ``body`` as the document body."""
    overrides = f'<Override PartName="/word/document.xml" ContentType="{main_type}"/>'
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f"{overrides}</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W_NS}"><w:body>{body}'
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>'
        ),
        "docProps/core.xml": _CORE.format(creator=creator),
    }
    if footer is not None:
        parts["word/footer1.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><w:ftr xmlns:w="{W_NS}">{footer}</w:ftr>'
        )
    parts.update(extra_parts or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NoSuchKey(Exception):
    """Shaped like botocore's ClientError for a missing key."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeVault:
    """A boto3-shaped client over a dict, one bucket. ``fail`` makes a call raise."""

    def __init__(self, objects: dict[str, bytes] | None = None, bucket: str = "cfg"):
        self.bucket = bucket
        self.objects: dict[str, bytes] = dict(objects or {})
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail: set[str] = set()

    def _guard(self, op: str) -> None:
        if op in self.fail:
            raise RuntimeError(f"vault {op} is down")

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803 (boto3 kwargs)
        self._guard("get")
        if Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str = ""):  # noqa: N803
        self._guard("put")
        self.objects[Key] = bytes(Body)
        self.puts.append(Key)
        return {}

    def delete_object(self, *, Bucket: str, Key: str):  # noqa: N803
        self._guard("delete")
        self.objects.pop(Key, None)
        self.deletes.append(Key)
        return {}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken: str | None = None):  # noqa: N803
        self._guard("list")
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {
            "Contents": [{"Key": k, "ETag": sha(self.objects[k])[:32]} for k in keys],
            "IsTruncated": False,
        }
