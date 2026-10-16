"""Read indexed normative pages for a review; no normative decisions are automated."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fire_audit.core import FireAuditApp

p = argparse.ArgumentParser()
p.add_argument('document')
p.add_argument('--find', nargs='*', default=[])
p.add_argument('--pages', nargs='*', type=int, default=[])
p.add_argument('--render', action='store_true')
args = p.parse_args()
app = FireAuditApp(Path(__file__).resolve().parents[1])
with app.norms._connect(read_only=True) as conn:
    rows = conn.execute('SELECT relative_path, extraction_json FROM documents').fetchall()
matches = [(path, raw) for path, raw in rows if args.document.lower() in path.lower()]
for path, raw in matches:
    print('DOCUMENT:', path)
    pages = json.loads(raw or '{}').get('pages', [])
    if args.find:
        for page in pages:
            hits = [term for term in args.find if term.lower() in page.get('text', '').lower()]
            if hits:
                print('PAGE', page['page'], 'TERMS', hits)
    for page in args.pages:
        result = app.norms.read_document(path, page=page, max_chars=40000)
        print(json.dumps({k: v for k, v in result.items() if k not in ('page_metadata', 'letter_references_in_document')}, ensure_ascii=False))
        if args.render:
            print(json.dumps(app.norms.render_page(path, page), ensure_ascii=False))
