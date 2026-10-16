"""Read-only candidate search in indexed normative pages; results are not proof."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fire_audit.core import FireAuditApp

app = FireAuditApp(Path(__file__).resolve().parents[1])
terms = [word.lower() for word in sys.argv[1:]]
with app.norms._connect(read_only=True) as conn:
    rows = conn.execute('SELECT relative_path, extraction_json FROM documents').fetchall()
for path, raw in rows:
    if not path.startswith('письма ВНИИПО/'):
        continue
    for page in json.loads(raw or '{}').get('pages', []):
        body = page.get('text', '')
        lowered = body.lower()
        if all(term in lowered for term in terms):
            print(json.dumps({'path': path, 'page': page['page'], 'text': body}, ensure_ascii=False))
