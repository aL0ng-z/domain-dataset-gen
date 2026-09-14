"""Safe, local mechanism reproductions for review of commit 019f9ea7.
These are extracted/normalized logic, not imports of the full repository.
No external services or production data are contacted.
"""
from __future__ import annotations
import asyncio
import contextlib
import importlib.metadata
import json
import re
import socket
import sqlite3
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url, URL

RESULTS = []

def record(name, observed, **details):
    assert observed, f"Mechanism did not reproduce: {name}"
    RESULTS.append({"test": name, "bug_observed": True, **details})


def migration_row_access():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT 'item-1' AS curated_item_id")).fetchone()
        try:
            row["curated_item_id"]  # T09 _backfill_curated_revisions access pattern
        except TypeError as exc:
            record("migration_row_access", True, exception=type(exc).__name__, message=str(exc),
                   corrected=row._mapping["curated_item_id"])
        else:
            record("migration_row_access", False)
    engine.dispose()


def register_two_unique_matches():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT UNIQUE)"))
        conn.execute(sa.text("INSERT INTO users VALUES(1,'alice','a@example.test'),(2,'bob','b@example.test')"))
        try:
            conn.execute(sa.text("SELECT id FROM users WHERE username=:name OR email=:email"),
                         {"name": "alice", "email": "b@example.test"}).scalar_one_or_none()
        except sa.exc.MultipleResultsFound as exc:
            record("register_two_unique_matches", True, exception=type(exc).__name__)
        else:
            record("register_two_unique_matches", False)
    engine.dispose()


def delete_object_before_fk():
    # SQLite confirms the rollback-vs-external-side-effect mechanism only;
    # it does not simulate all PostgreSQL cascades or repository migrations.
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'original.pdf'
        source.write_bytes(b'%PDF-local-test')
        db = sqlite3.connect(':memory:')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('CREATE TABLE documents(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE evidence_links(id INTEGER PRIMARY KEY, document_id INTEGER REFERENCES documents(id))')
        db.execute('INSERT INTO documents VALUES(1)')
        db.execute('INSERT INTO evidence_links VALUES(1,1)')
        db.commit()
        source.unlink()  # document_service performs object deletion before DB deletion
        error = None
        try:
            db.execute('DELETE FROM documents WHERE id=1')
            db.commit()
        except sqlite3.IntegrityError as exc:
            error = str(exc)
            db.rollback()
        exists = db.execute('SELECT count(*) FROM documents').fetchone()[0] == 1
        record('delete_object_before_fk', exists and not source.exists() and error is not None,
               database_document_preserved=exists, object_preserved=source.exists(), db_error=error)
        db.close()


def unicode_backfill_offsets():
    content = 'e\u0301 x'  # 4 original code points, 3 after NFC
    quote = 'x'
    idx = unicodedata.normalize('NFC', content).find(unicodedata.normalize('NFC', quote))
    end = idx + len(unicodedata.normalize('NFC', quote))
    actual = content[idx:end]
    record('unicode_backfill_offsets', actual != quote,
           stored_start=idx, stored_end=end, expected_quote=quote, actual_original_slice=actual,
           correct_start=content.index(quote))


def raw_page_regex():
    # Extraction pattern from _apply_overlap; physical page is fixture metadata, not a parsed PDF.
    content = 'This paragraph is physically on PDF page two. See Page 888 for background.'
    pattern = re.compile(r'(?:page|Page|PAGE)\s*(\d+)')
    inferred = sorted(set(int(m.group(1)) for m in pattern.finditer(content)))
    record('raw_page_regex', inferred == [888], physical_pdf_page=2, inferred_source_pages=inferred)


def export_input_loss():
    # _format_messages and _format_sharegpt field projection, after validator normalization.
    content = {'instruction': 'Answer using the input', 'question': 'Answer using the input',
               'input': 'UNIQUE_CONTEXT_MARKER: pressure is 12 Pa', 'output': '12 Pa', 'answer': '12 Pa'}
    messages = [
        {'role': 'system', 'content': content.get('system', '')},
        {'role': 'user', 'content': content.get('instruction', content.get('question', ''))},
        {'role': 'assistant', 'content': content.get('output', content.get('answer', ''))},
    ]
    conversations = [
        {'from': 'human', 'value': content.get('instruction', content.get('question', ''))},
        {'from': 'gpt', 'value': content.get('output', content.get('answer', ''))},
    ]
    record('export_input_loss', 'UNIQUE_CONTEXT_MARKER' not in json.dumps([messages, conversations]),
           input_present=True, messages_context_preserved=False, sharegpt_context_preserved=False)


def malformed_json_shape():
    accepted = [json.loads(raw) for raw in ['{}', '[]', 'null', '{"unexpected": 1}']]
    valid_qa = lambda x: isinstance(x, dict) and all(isinstance(x.get(k), str) and x[k].strip() for k in ('question','answer'))
    record('malformed_json_shape', not any(valid_qa(x) for x in accepted),
           json_parse_accepts=accepted, valid_qa_count=0)


def database_url_password():
    raw = 'postgresql+asyncpg://reviewer:review@pass@localhost:5432/datasetgen'
    parsed = make_url(raw)
    corrected = URL.create('postgresql+asyncpg', username='reviewer', password='review@pass',
                           host='localhost', port=5432, database='datasetgen')
    record('database_url_password', parsed.host != 'localhost' or parsed.password != 'review@pass',
           parsed_host=parsed.host, parsed_password=parsed.password, corrected_host=corrected.host)


async def ws_connection_snapshot_race():
    # The map replacement and suspension point from ConnectionManager._broadcast.
    connections = {'project': {'socket-A': 'user-A'}}
    entered, resume = asyncio.Event(), asyncio.Event()
    async def broadcast():
        live = {}
        for ws, user_id in list(connections['project'].items()):
            entered.set()
            await resume.wait()  # _verify_ws_access DB await
            live[ws] = user_id
        connections['project'] = live
    task = asyncio.create_task(broadcast())
    await entered.wait()
    connections['project']['socket-B'] = 'user-B'  # concurrent connect
    resume.set()
    await task
    record('ws_connection_snapshot_race', 'socket-B' not in connections['project'],
           remaining_connections=list(connections['project']))


async def blocking_storage_heartbeat():
    ticks = []
    async def ticker():
        for _ in range(4):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)
    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    start = time.monotonic()
    time.sleep(0.10)  # surrogate for synchronous network I/O in async export handler
    end = time.monotonic()
    during = sum(start <= t <= end for t in ticks)
    await task
    record('blocking_storage_heartbeat', during == 0,
           heartbeat_ticks_during_block=during, blocking_seconds=round(end-start, 3))


async def websocket_preaccept_close():
    # Real loopback TCP handshake: no Starlette TestClient shortcut.
    import uvicorn
    import websockets
    from fastapi import FastAPI, WebSocket
    app = FastAPI()
    @app.websocket('/ws')
    async def denied(ws: WebSocket):
        await ws.close(code=4401)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='off', ws='websockets'))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started, 'Local ASGI server failed to start'
    status = None
    try:
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws'):
                raise AssertionError('Unexpected handshake success')
        except Exception as exc:
            response = getattr(exc, 'response', None)
            status = getattr(response, 'status_code', getattr(exc, 'status_code', None))
            record('websocket_preaccept_close', status == 403,
                   http_status=status, received_websocket_close_frame=False, exception=type(exc).__name__)
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
        sock.close()


async def async_main():
    await ws_connection_snapshot_race()
    await blocking_storage_heartbeat()
    await websocket_preaccept_close()


def main():
    for fn in [migration_row_access, register_two_unique_matches, delete_object_before_fk,
               unicode_backfill_offsets, raw_page_regex, export_input_loss, malformed_json_shape,
               database_url_password]:
        fn()
    asyncio.run(async_main())
    output = {
        'mode': 'local extracted-logic / mechanism reproductions, NOT full repository tests',
        'python': sys.version.split()[0],
        'packages': {n: importlib.metadata.version(n) for n in ['sqlalchemy','fastapi','starlette','uvicorn','websockets']},
        'total': len(RESULTS), 'bug_observed': sum(r['bug_observed'] for r in RESULTS),
        'results': RESULTS,
    }
    path = Path(__file__).with_name('backend-results.json')
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
