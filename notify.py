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

    def run_main(self, items):
        with mock.patch("notify.fetch_pn", return_value=items), \
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

    def run_main_with_send_results(self, items, results):
        with mock.patch("notify.fetch_pn", return_value=items), \
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
