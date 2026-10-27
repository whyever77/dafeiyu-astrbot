"""Local Bili companion integration. API fixtures never contact a real account."""
from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/bili_activity'))
from bili_api import BiliAPI, BiliError, Executor, decode_danmaku_xml, private_json
from login import login_cookies
from service import create_server, validated_args

BVID = 'BV1xx411c7mD'
INFO = {'bvid': BVID, 'aid': 123, 'title': 'fixture title', 'owner': {'mid': 5},
        'pages': [{'cid': 11, 'page': 1, 'part': 'first', 'duration': 120},
                  {'cid': 22, 'page': 2, 'part': 'second', 'duration': 180}]}
COOKIES = {'SESSDATA': 'secret-session-fixture', 'bili_jct': 'secret-csrf-fixture', 'DedeUserID': '42'}


class FixtureAPI(BiliAPI):
    def __init__(self, folder):
        super().__init__(Path(folder) / 'cookies.json')
        private_json(self.cookie_file, {'cookies': COOKIES})
        self.calls = []
        self.posts = []
        self.timeout_post = False
        self.followed = False
        self.members = {100}
        self.folders = {100: {'id': 100, 'title': 'old', 'upper': {'mid': 42}, 'attr': 0},
                        200: {'id': 200, 'title': 'target', 'upper': {'mid': 42}, 'attr': 1}}
        self.reply = {'rpid': 900, 'oid': 123, 'mid': 42, 'root': 0, 'parent': 0, 'content': {'message': 'text'}}
        self.info = copy.deepcopy(INFO)

    async def request(self, path, params=None, method='GET', data=None, **kwargs):
        self.calls.append((path, params, method, data))
        if method == 'POST':
            self.posts.append((path, data.copy()))
            if self.timeout_post:
                raise TimeoutError('DO NOT LEAK secret-session-fixture')
            if path == '/x/relation/modify':
                self.followed = data['act'] == 1
                return {}
            if path == '/x/v3/fav/resource/deal':
                if data['add_media_ids']:
                    self.members.add(int(data['add_media_ids']))
                if data['del_media_ids']:
                    self.members.discard(int(data['del_media_ids']))
                return {}
            if path == '/x/v3/fav/resource/move':
                self.members.discard(data['src_media_id'])
                self.members.add(data['tar_media_id'])
                return {}
            if path == '/x/v3/fav/folder/add':
                self.folders[300] = {'id': 300, 'title': data['title'], 'upper': {'mid': 42}, 'attr': 1}
                return {'id': 300}
            if path == '/x/v3/fav/folder/edit':
                self.folders[data['media_id']]['title'] = data['title']
                return {}
            if path == '/x/v2/reply/add':
                self.reply = {'rpid': 901, 'oid': data['oid'], 'mid': 42,
                              'root': data.get('root', 0), 'parent': data.get('parent', 0),
                              'content': {'message': data['message']}}
                return {'rpid': 901}
            raise AssertionError(path)
        if path == '/x/web-interface/view':
            return copy.deepcopy(self.info)
        if path == '/x/web-interface/nav':
            return {'isLogin': True, 'mid': 42, 'uname': 'fixture', 'wbi_img': {
                'img_url': 'https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png',
                'sub_url': 'https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png'}}
        if path == '/x/v2/reply/wbi/main':
            params = dict(params, next=int(params.get('next', 0)))
            return {'replies': [self.reply], 'cursor': {'next': params['next'] + 1, 'is_end': params['next'] >= 2}}
        if path == '/x/v2/reply/reply':
            return {'root': self.reply, 'replies': [self.reply], 'page': {'count': 41}}
        if path == '/x/v2/reply/detail':
            return {'root': self.reply}
        if path == '/x/relation':
            return {'attribute': 2 if self.followed else 0}
        if path == '/x/relation/followings':
            return {'list': [{'mid': 5}], 'total': 1}
        if path == '/x/v3/fav/folder/info':
            return copy.deepcopy(self.folders[params['media_id']])
        if path == '/x/v3/fav/folder/created/list-all':
            return {'list': [dict(x, fav_state=int(x['id'] in self.members)) for x in self.folders.values()]}
        if path == '/x/v3/fav/resource/list':
            return {'medias': [dict(bvid=BVID, id=123)], 'has_more': False}
        raise AssertionError(path)

    async def signed(self, path, params, **kwargs):
        if path == '/x/player/wbi/v2':
            return {'subtitle': {'subtitles': [{'lan': 'zh-CN', 'subtitle_url': 'https://aisubtitle.hdslb.com/a.json'}]}}
        if path == '/x/space/wbi/acc/info':
            return {'mid': params['mid'], 'name': 'fixture UP'}
        if path == '/x/v2/reply/wbi/main':
            return await super().signed(path, params, **kwargs)
        raise AssertionError(path)

    async def get_bytes(self, url, limit):
        if url.endswith('.xml'):
            return b'<i><d p="0.5,1,25,1,0,0,u,10">first</d><d p="10.5,1,25,1,0,0,u,11">later</d></i>'
        return json.dumps({'body': [{'from': 2, 'to': 5, 'content': 'caption'}]}).encode()


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = FixtureAPI(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_page_resolution_and_cid_binding(self):
        result = await self.api.read('info', {'bvid': BVID, 'page': 2})
        self.assertEqual((result['identity']['page'], result['identity']['cid']), (2, 22))
        parts = await self.api.read('info', {'bvid': BVID})
        self.assertIsNone(parts['cid'])
        self.assertEqual(len(parts['identity']['pages']), 2)
        for args in ({'bvid': BVID, 'page': 2, 'cid': 11}, {'bvid': BVID, 'page': 9}):
            with self.assertRaises(ValueError):
                await self.api.read('info', args)
        with self.assertRaises(ValueError):
            await self.api.read('subtitles', {'bvid': BVID})
        self.api.info['bvid'] = 'BV1Q541167Qg'
        with self.assertRaises(BiliError):
            await self.api.identity(BVID, 11)

    async def test_subtitles_danmaku_times_and_pagination(self):
        args = {'bvid': BVID, 'cid': 22}
        subtitles = await self.api.read('subtitles', args)
        self.assertEqual(subtitles['identity']['cid'], 22)
        self.assertEqual(subtitles['subtitles'], [{'from': 2, 'to': 5, 'content': 'caption'}])
        dm = await self.api.read('danmaku', args | {'start': 0, 'end': 15, 'limit': 1})
        self.assertEqual(dm['items'][0]['at'], 0.5)
        self.assertEqual(dm['next_offset'], 1)
        next_dm = await self.api.read('danmaku', args | {'offset': dm['next_offset']})
        self.assertEqual(next_dm['items'][0]['at'], 10.5)
        comments = await self.api.read('comments', args | {'cursor': 1})
        self.assertEqual(comments['next_cursor'], 2)
        end = await self.api.read('comments', args | {'cursor': comments['next_cursor']})
        self.assertIsNone(end['next_cursor'])
        replies = await self.api.read('replies', args | {'root_rpid': 900, 'page': 2})
        self.assertEqual(replies['next_page'], 3)
        call = self.api.calls[-1]
        self.assertEqual(call[1]['oid'], 123)
        self.assertEqual(call[1]['pn'], 2)
        self.assertEqual(call[1]['root'], 900)

    async def test_all_account_actions_verified_and_replay_safe(self):
        ledger = Executor(self.api, Path(self.temp.name) / 'operations.db')
        actions = [
            ('comment', {'bvid': BVID, 'cid': 11, 'message': 'specific text'}),
            ('reply', {'bvid': BVID, 'cid': 11, 'message': 'a reply', 'root_rpid': 901, 'parent_rpid': 901}),
            ('follow', {'uid': 5}), ('unfollow', {'uid': 5}),
            ('favorite_add', {'bvid': BVID, 'cid': 11, 'folder_id': 200}),
            ('favorite_remove', {'bvid': BVID, 'cid': 11, 'folder_id': 200}),
            ('favorite_move', {'bvid': BVID, 'cid': 11, 'source_folder_id': 100, 'target_folder_id': 200}),
            ('folder_create', {'title': 'new folder'}),
            ('folder_rename', {'folder_id': 300, 'title': 'renamed'}),
        ]
        try:
            for index, (action, args) in enumerate(actions):
                with self.subTest(action=action):
                    args['account_uid'] = '42'
                    key = f'operation-{index}'
                    result = await ledger.execute(key, action, args)
                    self.assertTrue(result['verified'], result)
                    count = len(self.api.posts)
                    replay = await ledger.execute(key, action, args)
                    self.assertTrue(replay['replayed'])
                    self.assertEqual(len(self.api.posts), count)
            with self.assertRaises(ValueError):
                await ledger.execute('operation-0', 'comment', {'bvid': BVID, 'cid': 11, 'message': 'changed'})
            self.assertEqual(len(self.api.posts), len(actions))
        finally:
            ledger.close()

    async def test_unknown_write_outcome_survives_restart_without_post_retry(self):
        path = Path(self.temp.name) / 'operations.db'
        self.api.timeout_post = True
        ledger = Executor(self.api, path)
        args = {'uid': 5, 'account_uid': '42'}
        result = await ledger.execute('unknown-operation', 'follow', args)
        self.assertEqual(result['status'], 'uncertain')
        self.assertNotIn(COOKIES['SESSDATA'], json.dumps(result))
        ledger.close()
        ledger = Executor(self.api, path)
        try:
            result = await ledger.execute('unknown-operation', 'follow', args)
            self.assertEqual(result['status'], 'uncertain')
            self.assertEqual(len(self.api.posts), 1)
        finally:
            ledger.close()

    async def test_wrong_account_and_foreign_folder_stop_before_post(self):
        with self.assertRaises(BiliError):
            await self.api.prepare_write('follow', {'uid': 5, 'account_uid': '999'})
        self.api.folders[100]['upper']['mid'] = 999
        with self.assertRaises(BiliError):
            await self.api.prepare_write('favorite_add', {'bvid': BVID, 'cid': 11, 'folder_id': 100})
        self.assertEqual(self.api.posts, [])

    async def test_readback_rejects_wrong_comment_content_and_thread(self):
        self.api.reply.update(rpid=901, root=900, parent=900, content={'message': 'exact'})
        ident = await self.api.identity(BVID, 11)
        value = await self.api.verify('reply', {'message': 'exact', 'root_rpid': 900, 'parent_rpid': 901}, {'rpid': 901}, ident)
        self.assertFalse(value['verified'])
        value = await self.api.verify('comment', {'message': 'not exact'}, {'rpid': 901}, ident)
        self.assertFalse(value['verified'])


class CredentialTests(unittest.TestCase):
    def test_qr_set_cookie_and_legacy_callback(self):
        cookie = SimpleCookie()
        for k, v in COOKIES.items():
            cookie[k] = v
        self.assertEqual(login_cookies(cookie, {}), COOKIES)
        from urllib.parse import urlencode
        callback = 'https://passport.bilibili.com/callback?' + urlencode(COOKIES)
        self.assertEqual(login_cookies({}, {'url': callback}), COOKIES)
        legacy = {'cookie_info': {'cookies': [{'name': k, 'value': v} for k, v in COOKIES.items()]}}
        self.assertEqual(login_cookies({}, legacy), COOKIES)
        for payload in ({}, {'url': callback.replace('passport.bilibili.com', 'evil.test')}):
            with self.assertRaises(BiliError):
                login_cookies({}, payload)

    def test_credentials_private_and_media_headers_do_not_include_them(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as output:
            api = FixtureAPI(folder)
            self.assertNotIn('Cookie', api.headers(False))
            self.assertEqual(output.getvalue(), '')
            if os.name != 'nt':
                self.assertEqual(api.cookie_file.stat().st_mode & 0o777, 0o600)
            else:
                # Windows protects the file with ACLs; POSIX mode bits are not
                # meaningful there.
                self.assertTrue(api.cookie_file.is_file())

    def test_untrusted_sources_and_entity_payloads_rejected(self):
        for url in ('https://bilivideo.com.evil.test/a', 'http://127.0.0.1/a', 'file:///etc/passwd', 'https://u:p@a.bilivideo.com/a'):
            with self.assertRaises(BiliError):
                BiliAPI.media_url(url)
        with self.assertRaises(BiliError):
            decode_danmaku_xml(b'<!DOCTYPE i><i/>')
        self.assertEqual(decode_danmaku_xml(b'<i><d p="nan,1">bad</d><d p="-1,1">bad</d></i>'), [])
        with self.assertRaises(ValueError):
            validated_args('follow', {'uid': 5, 'csrf': 'not allowed'}, '42')


def result_json(result):
    if result.isError:
        raise AssertionError(result)
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(x.text for x in result.content if x.type == 'text'))


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_streamable_http_initialize_tools_preview_execute_and_errors(self):
        import uvicorn
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        class Phone:
            async def run(self, operation, args):
                return {'online': True, 'device_id': 'emulator-5554', 'identity_verified': False}

        with tempfile.TemporaryDirectory() as folder:
            api = FixtureAPI(folder)
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = create_server(folder, port=port, api=api, phone=Phone())
            app = server.streamable_http_app()
            runner = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='critical', access_log=False))
            task = asyncio.create_task(runner.serve(sockets=[sock]))
            try:
                for _ in range(100):
                    if runner.started:
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(runner.started)
                async with streamablehttp_client(f'http://127.0.0.1:{port}/mcp') as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        names = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertEqual(names, {'bili_read', 'bili_preview', 'bili_execute', 'bili_operation', 'bili_prepare_video', 'bili_phone'})
                        info = result_json(await session.call_tool('bili_read', {'operation': 'info', 'args': {'bvid': BVID, 'page': 2}}))
                        self.assertEqual(info['identity']['cid'], 22)
                        preview = result_json(await session.call_tool('bili_preview', {'action': 'comment', 'args': {'bvid': BVID, 'cid': 11, 'message': 'public text'}}))
                        self.assertEqual(preview['args']['account_uid'], '42')
                        self.assertEqual(preview['target']['title'], 'fixture title')
                        self.assertNotIn(COOKIES['bili_jct'], json.dumps(preview))
                        self.assertEqual(api.posts, [])
                        result = result_json(await session.call_tool('bili_execute', {'operation_id': 'protocol-operation', 'action': 'comment', 'args': preview['args']}))
                        self.assertTrue(result['verified'])
                        await session.call_tool('bili_execute', {'operation_id': 'protocol-operation', 'action': 'comment', 'args': preview['args']})
                        self.assertEqual(len(api.posts), 1)
                        api.timeout_post = True
                        error = result_json(await session.call_tool('bili_execute', {'operation_id': 'protocol-error', 'action': 'follow', 'args': {'uid': 7, 'account_uid': '42'}}))
                        self.assertFalse(error['verified'])
                        self.assertNotIn(COOKIES['SESSDATA'], json.dumps(error))
                        bad = result_json(await session.call_tool('bili_preview', {'action': 'follow', 'args': {'uid': 7, 'cookie': 'DO NOT LOG'}}))
                        self.assertFalse(bad['ok'])
                        self.assertNotIn('DO NOT LOG', json.dumps(bad))
                        phone = result_json(await session.call_tool('bili_phone', {'operation': 'status'}))
                        self.assertTrue(phone['online'])
            finally:
                runner.should_exit = True
                await asyncio.wait_for(task, 5)
                sock.close()


if __name__ == '__main__':
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('mcp').setLevel(logging.WARNING)
    unittest.main(verbosity=2)
