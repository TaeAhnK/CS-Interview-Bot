"""Run on Windows with .venv/Scripts/python.exe -m scripts.check_webview2."""

import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event

import webview
import webview.util

from bot.app import APP_HOST, _configure_webview2


APP_URL = f"https://{APP_HOST}/index.html"
FIXTURE_HOST = "cs-interview-bot-fixture.invalid"
FIXTURE_URL = f"https://{FIXTURE_HOST}/index.html"
TIMEOUT = 8


def main() -> None:
    received = Queue()
    navigations = Queue()
    dispatches = []
    failures = []
    completed = Event()
    allow_fixture_navigation = Event()
    original_dispatch = webview.util.js_bridge_call
    restore_backend = None

    def read_canary():
        return "ready"

    def unregistered_canary():
        raise AssertionError("The unregistered canary reached Python")

    def observe_dispatch(window, name, params, callback_id):
        dispatches.append(name)
        original_dispatch(window, name, params, callback_id)

    with TemporaryDirectory(prefix="csib-webview2-") as directory:
        assets = Path(directory)
        (assets / "index.html").write_text("<!doctype html><title>WebView2 check</title>", encoding="utf-8")
        window = webview.create_window("CS Interview Bot WebView2 integration check", APP_URL)
        window.expose(read_canary, unregistered_canary)

        def configure(renderer):
            nonlocal restore_backend
            try:
                if renderer != "edgechromium":
                    raise RuntimeError("Microsoft Edge WebView2 Runtime is required")
                from webview.platforms import edgechromium
                from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind

                restore_backend = _configure_webview2(window, APP_URL, assets, {read_canary.__name__})
                app_backend = edgechromium.EdgeChrome

                class ObservedWebView(app_backend):
                    def on_webview_ready(self, sender, args):
                        if args.IsSuccess:
                            sender.CoreWebView2.SetVirtualHostNameToFolderMapping(
                                FIXTURE_HOST, str(assets), CoreWebView2HostResourceAccessKind.Deny
                            )
                        super().on_webview_ready(sender, args)

                    def on_navigation_start(self, sender, args):
                        uri = str(args.Uri)
                        # Reach the source guard independently after verifying the normal navigation guard.
                        if uri == FIXTURE_URL and allow_fixture_navigation.is_set():
                            return
                        super().on_navigation_start(sender, args)
                        if uri == FIXTURE_URL:
                            navigations.put(bool(args.Cancel))

                    def on_script_notify(self, sender, args):
                        before = len(dispatches)
                        super().on_script_notify(sender, args)
                        message = json.loads(args.WebMessageAsJson)
                        if isinstance(message, list) and len(message) == 3 and message[2] == "271828":
                            received.put((str(args.Source), message[0], dispatches[before:]))

                edgechromium.EdgeChrome = ObservedWebView
                return True
            except Exception as error:
                failures.append(error)
                return False

        def check_message(source, name, expected_dispatches):
            message = json.dumps([name, "[]", "271828"])
            window.run_js(
                f"window.pywebview._returnValuesCallbacks[{json.dumps(name)}] = {{271828: () => {{}}}};"
                f"window.chrome.webview.postMessage({message});"
            )
            assert received.get(timeout=TIMEOUT) == (source, name, expected_dispatches)

        def run_checks():
            try:
                assert window.events.loaded.wait(TIMEOUT), "The app document did not load"
                assert window.get_current_url() == APP_URL
                check_message(APP_URL, "read_canary", ["read_canary"])
                print("PASS: registered method reached Python", flush=True)
                check_message(APP_URL, "unregistered_canary", [])
                check_message(APP_URL, "read_canary", ["read_canary"])
                print("PASS: unregistered method blocked; registered method still works", flush=True)

                window.run_js(f"location.assign({json.dumps(FIXTURE_URL)})")
                assert navigations.get(timeout=TIMEOUT), "Navigation was not cancelled"
                assert window.evaluate_js("location.href") == APP_URL
                print("PASS: navigation outside the app document cancelled", flush=True)

                allow_fixture_navigation.set()
                window.load_url(FIXTURE_URL)
                assert window.events.loaded.wait(TIMEOUT), "The fixture document did not load"
                assert window.get_current_url() == FIXTURE_URL
                check_message(FIXTURE_URL, "read_canary", [])
                print("PASS: message from the fixture origin reached WebView2 but not Python", flush=True)
                completed.set()
            except Exception as error:
                failures.append(error)
            finally:
                if window.events.shown.is_set():
                    window.destroy()

        window.events.initialized += configure
        webview.util.js_bridge_call = observe_dispatch
        try:
            webview.start(run_checks, gui="edgechromium")
        finally:
            webview.util.js_bridge_call = original_dispatch
            if restore_backend is not None:
                restore_backend()

    if failures:
        raise failures[0]
    if not completed.is_set():
        raise RuntimeError("WebView2 closed before the checks completed")


if __name__ == "__main__":
    main()
