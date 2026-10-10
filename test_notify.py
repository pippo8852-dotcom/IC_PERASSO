import json
import os
import tempfile
import unittest
from unittest import mock

import requests

import notify


def make_response(status=200, text=""):
    resp = mock.Mock()
    resp.status_code = status
    resp.text = text
    resp.content = text.encode("utf-8") if isinstance(text, str) else text
    resp.raise_for_status = mock.Mock()
    return resp


def table_html(*rows):
    body = "".join(f"<tr>{r}</tr>" for r in rows)
    return f"<html><body><table>{body}</table></body></html>"


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")
        patcher = mock.patch.object(notify, "STATE_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_state_file_missing_returns_empty_dict(self):
        self.assertEqual(notify.load_state(), {})

    def test_load_state_valid_file(self):
        with open(self.path, "w") as f:
            json.dump({"bacheca_8": ["1", "2"]}, f)
        self.assertEqual(notify.load_state(), {"bacheca_8": ["1", "2"]})

    def test_load_state_corrupted_json_returns_empty_dict(self):
        with open(self.path, "w") as f:
            f.write("{non è json")
        self.assertEqual(notify.load_state(), {})

    def test_load_state_empty_file_returns_empty_dict(self):
        open(self.path, "w").close()
        self.assertEqual(notify.load_state(), {})

    def test_save_then_load_roundtrip(self):
        state = {"bacheca_8": ["10", "20"]}
        notify.save_state(state)
        self.assertEqual(notify.load_state(), state)

    def test_save_state_error_does_not_raise(self):
        bad_path = os.path.join(self.tmp.name, "dir_inesistente", "state.json")
        with mock.patch.object(notify, "STATE_FILE", bad_path):
            notify.save_state({"a": 1})  # non deve sollevare eccezioni


class SendTelegramTests(unittest.TestCase):
    def test_missing_token_does_not_call_api(self):
        with mock.patch.object(notify, "TELEGRAM_TOKEN", None), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", "123"), \
             mock.patch("notify.requests.post") as post:
            notify.send_telegram_message("ciao")
        post.assert_not_called()

    def test_missing_chat_id_does_not_call_api(self):
        with mock.patch.object(notify, "TELEGRAM_TOKEN", "tok"), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", ""), \
             mock.patch("notify.requests.post") as post:
            notify.send_telegram_message("ciao")
        post.assert_not_called()

    def test_success_sends_expected_request(self):
        with mock.patch.object(notify, "TELEGRAM_TOKEN", "tok"), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", "123"), \
             mock.patch("notify.requests.post", return_value=make_response()) as post:
            notify.send_telegram_message("<b>ciao</b>")

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.telegram.org/bottok/sendMessage")
        self.assertEqual(kwargs["json"]["chat_id"], "123")
        self.assertEqual(kwargs["json"]["text"], "<b>ciao</b>")
        self.assertEqual(kwargs["json"]["parse_mode"], "HTML")
        self.assertTrue(kwargs["json"]["disable_web_page_preview"])
        self.assertIn("timeout", kwargs)

    def test_success_returns_true(self):
        with mock.patch.object(notify, "TELEGRAM_TOKEN", "tok"), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", "123"), \
             mock.patch("notify.requests.post", return_value=make_response()):
            self.assertIs(notify.send_telegram_message("ciao"), True)

    def test_missing_credentials_returns_false(self):
        with mock.patch.object(notify, "TELEGRAM_TOKEN", None), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", None):
            self.assertIs(notify.send_telegram_message("ciao"), False)

    def test_network_error_is_swallowed_and_returns_false(self):
        with mock.patch.object(notify, "TELEGRAM_TOKEN", "tok"), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", "123"), \
             mock.patch("notify.requests.post",
                        side_effect=requests.ConnectionError("down")):
            self.assertIs(notify.send_telegram_message("ciao"), False)

    def test_http_error_is_swallowed_and_returns_false(self):
        resp = make_response(400)
        resp.raise_for_status.side_effect = requests.HTTPError("400")
        with mock.patch.object(notify, "TELEGRAM_TOKEN", "tok"), \
             mock.patch.object(notify, "TELEGRAM_CHAT_ID", "123"), \
             mock.patch("notify.requests.post", return_value=resp):
            self.assertIs(notify.send_telegram_message("ciao"), False)


class FetchPnTests(unittest.TestCase):
    def fetch(self, html, status=200):
        with mock.patch("notify.requests.get",
                        return_value=make_response(status, html)):
            return notify.fetch_pn()

    def test_happy_path_extracts_items_from_table_rows(self):
        html = table_html(
            "<td>Circolare 1</td><td><a href='/bacheca-digitale/111/dettaglio'>Apri</a></td>",
            "<td>Circolare 2</td><td><a href='/bacheca-digitale/222/dettaglio'>Apri</a></td>",
        )
        items = self.fetch(html)

        self.assertEqual([i["id"] for i in items], ["111", "222"])
        self.assertIn("Circolare 1", items[0]["title"])
        self.assertEqual(
            items[0]["link"],
            "https://nuvola.madisoft.it/bacheca-digitale/111/documento/MIIC8GH008",
        )

    def test_request_uses_expected_url_and_user_agent(self):
        with mock.patch("notify.requests.get",
                        return_value=make_response(200, "<html></html>")) as get:
            notify.fetch_pn()
        args, kwargs = get.call_args
        self.assertEqual(
            args[0],
            "https://nuvola.madisoft.it/bacheca-digitale/bacheca/ricerca/MIIC8GH008/8/show",
        )
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertIn("timeout", kwargs)

    def test_row_title_whitespace_is_normalized(self):
        html = table_html(
            "<td>  Avviso \n   importante  </td><td><a href='/bacheca-digitale/5/x'>Apri</a></td>"
        )
        items = self.fetch(html)
        self.assertNotIn("\n", items[0]["title"])
        self.assertNotIn("  ", items[0]["title"])

    def test_long_title_is_truncated_to_200_chars(self):
        long_text = "A" * 500
        html = table_html(
            f"<td>{long_text}</td><td><a href='/bacheca-digitale/7/x'>Apri</a></td>"
        )
        items = self.fetch(html)
        self.assertEqual(len(items[0]["title"]), 200)
        self.assertTrue(items[0]["title"].endswith("..."))

    def test_title_of_exactly_200_chars_is_not_truncated(self):
        # Riga con solo testo da 200 caratteri: il link ha testo vuoto
        text = "B" * 200
        html = table_html(
            f"<td>{text}</td><td><a href='/bacheca-digitale/8/x'></a></td>"
        )
        items = self.fetch(html)
        self.assertEqual(items[0]["title"], text)

    def test_only_first_valid_link_per_row_is_used(self):
        html = table_html(
            "<td>Atto</td>"
            "<td><a href='/bacheca-digitale/10/a'>uno</a>"
            "<a href='/bacheca-digitale/20/b'>due</a></td>"
        )
        items = self.fetch(html)
        self.assertEqual([i["id"] for i in items], ["10"])

    def test_row_without_matching_link_is_ignored(self):
        html = table_html(
            "<th>Intestazione</th>",
            "<td>Altro</td><td><a href='/altra-sezione/99'>x</a></td>",
            "<td>Valido</td><td><a href='/bacheca-digitale/30/x'>Apri</a></td>",
        )
        items = self.fetch(html)
        self.assertEqual([i["id"] for i in items], ["30"])

    def test_duplicate_ids_are_removed_keeping_order(self):
        html = table_html(
            "<td>Primo</td><td><a href='/bacheca-digitale/1/x'>a</a></td>",
            "<td>Secondo</td><td><a href='/bacheca-digitale/2/x'>a</a></td>",
            "<td>Primo bis</td><td><a href='/bacheca-digitale/1/y'>a</a></td>",
        )
        items = self.fetch(html)
        self.assertEqual([i["id"] for i in items], ["1", "2"])
        self.assertIn("Primo", items[0]["title"])
        self.assertNotIn("bis", items[0]["title"])

    def test_fallback_when_no_table_rows(self):
        html = (
            "<html><body>"
            "<a href='/bacheca-digitale/41/x'>Comunicazione A</a>"
            "<a href='/bacheca-digitale/42/x'>Comunicazione B</a>"
            "<a href='/contatti'>Contatti</a>"
            "</body></html>"
        )
        items = self.fetch(html)
        self.assertEqual([i["id"] for i in items], ["41", "42"])
        self.assertEqual(items[0]["title"], "Comunicazione A")

    def test_fallback_used_when_rows_exist_but_none_match(self):
        html = (
            "<html><body><table><tr><td>niente link</td></tr></table>"
            "<a href='/bacheca-digitale/50/x'>Fuori tabella</a></body></html>"
        )
        items = self.fetch(html)
        self.assertEqual([i["id"] for i in items], ["50"])

    def test_fallback_default_title_when_anchor_has_no_text(self):
        html = "<html><body><a href='/bacheca-digitale/60/x'></a></body></html>"
        items = self.fetch(html)
        self.assertEqual(items[0]["title"], "Comunicazione PN 60")

    def test_non_200_status_returns_empty_list(self):
        self.assertEqual(self.fetch("<html></html>", status=500), [])

    def test_network_exception_returns_empty_list(self):
        with mock.patch("notify.requests.get",
                        side_effect=requests.Timeout("timeout")):
            self.assertEqual(notify.fetch_pn(), [])

    def test_empty_page_returns_empty_list(self):
        self.assertEqual(self.fetch(""), [])



RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>IC Perasso</title>
    <item>
      <title><![CDATA[Open day & iscrizioni]]></title>
      <link>https://icperasso.edu.it/open-day/</link>
      <guid isPermaLink="false">https://icperasso.edu.it/?p=200</guid>
      <dc:creator>admin</dc:creator>
    </item>
    <item>
      <title>  Sciopero   del 15 ottobre </title>
      <link>https://icperasso.edu.it/sciopero/</link>
      <guid isPermaLink="false">https://icperasso.edu.it/?p=199</guid>
    </item>
  </channel>
</rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>IC Perasso</title>
  <entry>
    <title>Gita a Milano</title>
    <link rel="alternate" href="https://icperasso.edu.it/gita/"/>
    <id>tag:icperasso.edu.it,2026:gita</id>
  </entry>
</feed>"""


class FetchNovitaTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("notify.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, xml, status=200):
        resp = make_response(status, xml)
        if status >= 400:
            resp.raise_for_status.side_effect = requests.HTTPError(str(status))
        with mock.patch("notify.requests.get", return_value=resp):
            return notify.fetch_novita()

    def test_rss_happy_path(self):
        items = self.fetch(RSS_SAMPLE)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "https://icperasso.edu.it/?p=200")
        self.assertEqual(items[0]["title"], "Open day & iscrizioni")
        self.assertEqual(items[0]["link"], "https://icperasso.edu.it/open-day/")

    def test_request_uses_feed_url(self):
        with mock.patch("notify.requests.get",
                        return_value=make_response(200, RSS_SAMPLE)) as get:
            notify.fetch_novita()
        self.assertEqual(get.call_args.args[0], "https://icperasso.edu.it/feed/")
        self.assertIn("Mozilla/5.0", get.call_args.kwargs["headers"]["User-Agent"])
        self.assertIn("timeout", get.call_args.kwargs)

    def test_title_whitespace_is_normalized(self):
        items = self.fetch(RSS_SAMPLE)
        self.assertEqual(items[1]["title"], "Sciopero del 15 ottobre")

    def test_atom_feed_is_supported(self):
        items = self.fetch(ATOM_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "tag:icperasso.edu.it,2026:gita")
        self.assertEqual(items[0]["link"], "https://icperasso.edu.it/gita/")
        self.assertEqual(items[0]["title"], "Gita a Milano")

    def test_missing_guid_falls_back_to_link(self):
        xml = ("<rss><channel><item><title>A</title>"
               "<link>https://x.it/a</link></item></channel></rss>")
        items = self.fetch(xml)
        self.assertEqual(items[0]["id"], "https://x.it/a")

    def test_missing_title_gets_default(self):
        xml = ("<rss><channel><item><link>https://x.it/a</link>"
               "</item></channel></rss>")
        self.assertEqual(self.fetch(xml)[0]["title"], "Nuova notizia")

    def test_item_without_link_is_skipped(self):
        xml = ("<rss><channel><item><title>Senza link</title></item>"
               "<item><title>Ok</title><link>https://x.it/ok</link></item>"
               "</channel></rss>")
        items = self.fetch(xml)
        self.assertEqual([i["title"] for i in items], ["Ok"])

    def test_long_title_is_truncated(self):
        xml = ("<rss><channel><item><title>" + "A" * 500 + "</title>"
               "<link>https://x.it/a</link></item></channel></rss>")
        title = self.fetch(xml)[0]["title"]
        self.assertEqual(len(title), 200)
        self.assertTrue(title.endswith("..."))

    def test_duplicate_guids_are_removed(self):
        xml = ("<rss><channel>"
               "<item><title>A</title><link>https://x.it/a</link><guid>1</guid></item>"
               "<item><title>A bis</title><link>https://x.it/a2</link><guid>1</guid></item>"
               "</channel></rss>")
        self.assertEqual(len(self.fetch(xml)), 1)

    def test_feed_without_items_returns_empty_list(self):
        self.assertEqual(self.fetch("<rss><channel></channel></rss>"), [])

    def test_malformed_xml_returns_empty_list(self):
        self.assertEqual(self.fetch("<html><body>non è un feed"), [])

    def test_http_error_returns_empty_list(self):
        self.assertEqual(self.fetch("", status=403), [])

    def test_network_error_returns_empty_list(self):
        with mock.patch("notify.requests.get",
                        side_effect=requests.Timeout("timeout")):
            self.assertEqual(notify.fetch_novita(), [])

    def bad_gateway(self):
        resp = make_response(502, "")
        resp.raise_for_status.side_effect = requests.HTTPError("502 Bad Gateway")
        return resp

    def test_retries_same_url_after_502_then_succeeds(self):
        with mock.patch("notify.requests.get",
                        side_effect=[self.bad_gateway(), make_response(200, RSS_SAMPLE)]) as get:
            items = notify.fetch_novita()
        self.assertEqual(len(items), 2)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[0], get.call_args_list[1].args[0])
        self.sleep.assert_called_once()

    def test_falls_back_to_alternative_url_when_first_keeps_failing(self):
        responses = [self.bad_gateway()] * notify.FEED_ATTEMPTS + [make_response(200, RSS_SAMPLE)]
        with mock.patch("notify.requests.get", side_effect=responses) as get:
            items = notify.fetch_novita()
        self.assertEqual(len(items), 2)
        self.assertEqual(get.call_args_list[-1].args[0], notify.FEED_URLS[1])

    def test_malformed_first_url_falls_back_to_next(self):
        responses = [make_response(200, "<html>non xml"), make_response(200, "<html>ancora no"),
                     make_response(200, ATOM_SAMPLE)]
        with mock.patch("notify.requests.get", side_effect=responses):
            items = notify.fetch_novita()
        self.assertEqual(items[0]["title"], "Gita a Milano")

    def test_all_urls_failing_returns_empty_list_after_all_attempts(self):
        with mock.patch("notify.requests.get",
                        return_value=self.bad_gateway()) as get:
            self.assertEqual(notify.fetch_novita(), [])
        self.assertEqual(get.call_count, len(notify.FEED_URLS) * notify.FEED_ATTEMPTS)

    def test_no_sleep_after_last_attempt_of_a_url(self):
        with mock.patch("notify.requests.get", return_value=self.bad_gateway()):
            notify.fetch_novita()
        self.assertEqual(self.sleep.call_count,
                         len(notify.FEED_URLS) * (notify.FEED_ATTEMPTS - 1))


class DateHelpersTests(unittest.TestCase):
    def test_rss_date_utc_is_converted_to_italian_summer_time(self):
        self.assertEqual(notify.parse_feed_date("Sat, 10 Oct 2026 10:30:00 +0000"),
                         "10/10/2026 12:30")

    def test_rss_date_utc_is_converted_to_italian_winter_time(self):
        self.assertEqual(notify.parse_feed_date("Mon, 12 Jan 2026 10:30:00 +0000"),
                         "12/01/2026 11:30")

    def test_rss_date_with_offset(self):
        self.assertEqual(notify.parse_feed_date("Sat, 10 Oct 2026 12:30:00 +0200"),
                         "10/10/2026 12:30")

    def test_atom_iso_date_with_z(self):
        self.assertEqual(notify.parse_feed_date("2026-10-10T10:30:00Z"), "10/10/2026 12:30")

    def test_atom_iso_date_with_offset(self):
        self.assertEqual(notify.parse_feed_date("2026-10-10T12:30:00+02:00"),
                         "10/10/2026 12:30")

    def test_invalid_or_empty_feed_date_returns_none(self):
        for value in (None, "", "   ", "ieri sera", "32 Foo 2026"):
            self.assertIsNone(notify.parse_feed_date(value), value)

    def test_text_date_with_time(self):
        self.assertEqual(notify.extract_date_from_text("Circolare 5 pubblicata il 05/10/2026 alle 09:30"),
                         "05/10/2026 09:30")

    def test_text_date_with_dot_time(self):
        self.assertEqual(notify.extract_date_from_text("5/10/2026 ore 9.05"), "05/10/2026 09:05")

    def test_text_date_without_time(self):
        self.assertEqual(notify.extract_date_from_text("Avviso del 5/10/2026 per le famiglie"),
                         "05/10/2026")

    def test_text_without_date_returns_none(self):
        self.assertIsNone(notify.extract_date_from_text("Avviso senza data"))
        self.assertIsNone(notify.extract_date_from_text(""))
        self.assertIsNone(notify.extract_date_from_text(None))

    def test_text_with_impossible_date_returns_none(self):
        self.assertIsNone(notify.extract_date_from_text("Scadenza 31/02/2026"))

    def test_text_with_invalid_time_keeps_only_date(self):
        self.assertEqual(notify.extract_date_from_text("05/10/2026 ore 25:99"), "05/10/2026")


class FeedDateTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("notify.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, xml):
        with mock.patch("notify.requests.get", return_value=make_response(200, xml)):
            return notify.fetch_novita()

    def test_rss_pubdate_is_read(self):
        xml = ("<rss><channel><item><title>A</title><link>https://x.it/a</link>"
               "<pubDate>Sat, 10 Oct 2026 10:30:00 +0000</pubDate></item></channel></rss>")
        self.assertEqual(self.fetch(xml)[0]["date"], "10/10/2026 12:30")

    def test_atom_published_is_read(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>'
               '<link href="https://x.it/a"/><published>2026-10-10T10:30:00Z</published>'
               '</entry></feed>')
        self.assertEqual(self.fetch(xml)[0]["date"], "10/10/2026 12:30")

    def test_atom_falls_back_to_updated(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>'
               '<link href="https://x.it/a"/><updated>2026-10-10T10:30:00Z</updated>'
               '</entry></feed>')
        self.assertEqual(self.fetch(xml)[0]["date"], "10/10/2026 12:30")

    def test_missing_date_gives_none(self):
        xml = ("<rss><channel><item><title>A</title><link>https://x.it/a</link>"
               "</item></channel></rss>")
        self.assertIsNone(self.fetch(xml)[0]["date"])

    def test_unreadable_date_gives_none(self):
        xml = ("<rss><channel><item><title>A</title><link>https://x.it/a</link>"
               "<pubDate>boh</pubDate></item></channel></rss>")
        self.assertIsNone(self.fetch(xml)[0]["date"])


class PnDateTests(unittest.TestCase):
    def fetch(self, html_text):
        with mock.patch("notify.requests.get", return_value=make_response(200, html_text)):
            return notify.fetch_pn()

    def test_date_and_time_taken_from_row_text(self):
        html_text = table_html(
            "<td>Circolare 5</td><td>Pubblicata il 05/10/2026 alle 09:30</td>"
            "<td><a href='/bacheca-digitale/111/x'>Apri</a></td>")
        self.assertEqual(self.fetch(html_text)[0]["date"], "05/10/2026 09:30")

    def test_row_without_date_has_none(self):
        html_text = table_html(
            "<td>Circolare</td><td><a href='/bacheca-digitale/111/x'>Apri</a></td>")
        self.assertIsNone(self.fetch(html_text)[0]["date"])

    def test_fallback_items_have_no_date(self):
        html_text = "<html><body><a href='/bacheca-digitale/41/x'>A</a></body></html>"
        self.assertIsNone(self.fetch(html_text)[0]["date"])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")
        patcher = mock.patch.object(notify, "STATE_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def item(i):
        return {
            "id": str(i),
            "title": f"Titolo {i}",
            "link": f"https://example.com/{i}",
        }

    def run_main(self, items, feed_items=None, feed_enabled=False):
        sources = [dict(src, enabled=True) for src in notify.SOURCES] if feed_enabled \
            else notify.SOURCES
        with mock.patch("notify.fetch_pn", return_value=items), \
             mock.patch("notify.fetch_novita", return_value=feed_items or []), \
             mock.patch.object(notify, "SOURCES", sources), \
             mock.patch("notify.send_telegram_message") as send:
            notify.main()
        return send

    def read_state(self):
        with open(self.path) as f:
            return json.load(f)

    def test_first_run_sends_all_items_and_saves_state(self):
        send = self.run_main([self.item(3), self.item(2), self.item(1)])
        self.assertEqual(send.call_count, 3)
        self.assertEqual(set(self.read_state()["bacheca_8"]), {"1", "2", "3"})

    def test_items_are_sent_oldest_first(self):
        # il sito elenca dal più recente al più vecchio: si invia in ordine inverso
        send = self.run_main([self.item(3), self.item(2), self.item(1)])
        sent_texts = [c.args[0] for c in send.call_args_list]
        self.assertIn("Titolo 1", sent_texts[0])
        self.assertIn("Titolo 3", sent_texts[-1])

    def test_message_format_contains_title_and_link(self):
        send = self.run_main([self.item(1)])
        msg = send.call_args.args[0]
        self.assertIn("<b>", msg)
        self.assertIn("href='https://example.com/1'", msg)
        self.assertIn("Titolo 1", msg)

    def test_only_new_items_are_sent(self):
        with open(self.path, "w") as f:
            json.dump({"bacheca_8": ["1", "2"]}, f)
        send = self.run_main([self.item(3), self.item(2), self.item(1)])
        self.assertEqual(send.call_count, 1)
        self.assertIn("Titolo 3", send.call_args.args[0])
        self.assertEqual(set(self.read_state()["bacheca_8"]), {"1", "2", "3"})

    def test_no_new_items_sends_nothing_and_keeps_state(self):
        with open(self.path, "w") as f:
            json.dump({"bacheca_8": ["1"]}, f)
        send = self.run_main([self.item(1)])
        send.assert_not_called()
        self.assertEqual(self.read_state()["bacheca_8"], ["1"])

    def test_empty_fetch_sends_nothing_and_preserves_existing_state(self):
        with open(self.path, "w") as f:
            json.dump({"bacheca_8": ["1", "2"]}, f)
        send = self.run_main([])
        send.assert_not_called()
        self.assertEqual(self.read_state()["bacheca_8"], ["1", "2"])

    def test_other_keys_in_state_are_preserved(self):
        with open(self.path, "w") as f:
            json.dump({"altra_bacheca": ["x"], "bacheca_8": []}, f)
        self.run_main([self.item(1)])
        state = self.read_state()
        self.assertEqual(state["altra_bacheca"], ["x"])
        self.assertEqual(state["bacheca_8"], ["1"])

    def test_corrupted_state_file_is_treated_as_first_run(self):
        with open(self.path, "w") as f:
            f.write("rotto")
        send = self.run_main([self.item(1)])
        self.assertEqual(send.call_count, 1)

    def test_all_sources_are_enabled_by_default(self):
        self.assertTrue(all(s.get("enabled", True) for s in notify.SOURCES))
        self.assertEqual({s["key"] for s in notify.SOURCES},
                         {"bacheca_8", "feed_icperasso"})

    def test_disabled_feed_is_not_fetched_nor_sent_nor_saved(self):
        feed = [{"id": "g1", "title": "Notizia", "link": "https://x.it/n"}]
        sources = [dict(src, enabled=(src["key"] != "feed_icperasso"))
                   for src in notify.SOURCES]
        with mock.patch("notify.fetch_pn", return_value=[self.item(1)]), \
             mock.patch("notify.fetch_novita", return_value=feed) as fetch_feed, \
             mock.patch.object(notify, "SOURCES", sources), \
             mock.patch("notify.send_telegram_message") as send:
            notify.main()
        fetch_feed.assert_not_called()
        self.assertEqual(send.call_count, 1)
        self.assertIn("PN / Comunicazioni", send.call_args.args[0])
        state = self.read_state()
        self.assertEqual(state["bacheca_8"], ["1"])
        self.assertNotIn("feed_icperasso", state)

    def test_feed_items_are_sent_with_feed_label_and_saved_under_own_key(self):
        feed = [{"id": "g1", "title": "Notizia", "link": "https://x.it/n"}]
        send = self.run_main([], feed_items=feed, feed_enabled=True)
        self.assertEqual(send.call_count, 1)
        self.assertIn("News IC Perasso", send.call_args.args[0])
        state = self.read_state()
        self.assertEqual(state["feed_icperasso"], ["g1"])
        self.assertEqual(state["bacheca_8"], [])

    def test_both_sources_are_processed(self):
        feed = [{"id": "g1", "title": "Notizia", "link": "https://x.it/n"}]
        send = self.run_main([self.item(1)], feed_items=feed, feed_enabled=True)
        self.assertEqual(send.call_count, 2)
        state = self.read_state()
        self.assertEqual(state["bacheca_8"], ["1"])
        self.assertEqual(state["feed_icperasso"], ["g1"])

    def test_known_feed_items_are_not_resent(self):
        with open(self.path, "w") as f:
            json.dump({"feed_icperasso": ["g1"]}, f)
        feed = [{"id": "g1", "title": "Notizia", "link": "https://x.it/n"}]
        send = self.run_main([], feed_items=feed, feed_enabled=True)
        send.assert_not_called()

    def test_failure_in_one_source_does_not_block_the_other(self):
        feed = [{"id": "g1", "title": "Notizia", "link": "https://x.it/n"}]
        enabled = [dict(src, enabled=True) for src in notify.SOURCES]
        with mock.patch("notify.fetch_pn", return_value=[self.item(1)]), \
             mock.patch("notify.fetch_novita", return_value=feed), \
             mock.patch.object(notify, "SOURCES", enabled), \
             mock.patch("notify.send_telegram_message",
                        side_effect=[False, True]):
            notify.main()
        state = self.read_state()
        self.assertEqual(state["bacheca_8"], [])
        self.assertEqual(state["feed_icperasso"], ["g1"])

    def test_message_contains_date_line_when_available(self):
        item = dict(self.item(1), date="10/10/2026 12:30")
        send = self.run_main([item])
        msg = send.call_args.args[0]
        self.assertIn("\n🗓 10/10/2026 12:30\n\n<a href=", msg)
        self.assertLess(msg.index("🗓"), msg.index("<a href="))
        self.assertTrue(msg.endswith("</a>"))

    def test_message_has_no_date_line_when_missing(self):
        send = self.run_main([self.item(1)])
        self.assertNotIn("🗓", send.call_args.args[0])

    def test_feed_message_contains_date_line(self):
        feed = [{"id": "g1", "title": "Notizia", "link": "https://x.it/n",
                 "date": "10/10/2026 12:30"}]
        send = self.run_main([], feed_items=feed, feed_enabled=True)
        self.assertIn("🗓 10/10/2026 12:30", send.call_args.args[0])

    def test_special_characters_in_title_and_link_are_escaped(self):
        feed = [{"id": "g1", "title": "Open day <2026> & iscrizioni",
                 "link": "https://x.it/n?a=1&b=2"}]
        send = self.run_main([], feed_items=feed, feed_enabled=True)
        msg = send.call_args.args[0]
        self.assertIn("Open day &lt;2026&gt; &amp; iscrizioni", msg)
        self.assertIn("href='https://x.it/n?a=1&amp;b=2'", msg)

    def run_main_with_send_results(self, items, results):
        with mock.patch("notify.fetch_pn", return_value=items), \
             mock.patch("notify.fetch_novita", return_value=[]), \
             mock.patch("notify.send_telegram_message",
                        side_effect=results) as send:
            notify.main()
        return send

    def test_failed_send_is_not_saved_in_state(self):
        send = self.run_main_with_send_results([self.item(1)], [False])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.read_state()["bacheca_8"], [])

    def test_failed_item_is_retried_on_next_run(self):
        items = [self.item(1)]
        self.run_main_with_send_results(items, [False])
        send = self.run_main_with_send_results(items, [True])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.read_state()["bacheca_8"], ["1"])

    def test_failure_stops_loop_and_keeps_order(self):
        # Ordine di invio: 1, 2, 3. Il 2 fallisce: l'1 resta salvato,
        # il 3 non viene nemmeno tentato (così l'ordine è preservato).
        items = [self.item(3), self.item(2), self.item(1)]
        send = self.run_main_with_send_results(items, [True, False])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.read_state()["bacheca_8"], ["1"])

    def test_failure_does_not_wipe_previous_state(self):
        with open(self.path, "w") as f:
            json.dump({"bacheca_8": ["1"]}, f)
        self.run_main_with_send_results([self.item(2), self.item(1)], [False])
        self.assertEqual(self.read_state()["bacheca_8"], ["1"])

    def test_second_run_does_not_resend(self):
        items = [self.item(2), self.item(1)]
        self.run_main(items)
        send = self.run_main(items)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
