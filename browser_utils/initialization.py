# --- browser_utils/initialization.py ---
# Browser initialization utilities

import asyncio
import os
import time
import json
import logging
from typing import Optional, Any, Dict, Tuple

from playwright.async_api import Page as AsyncPage, Browser as AsyncBrowser, BrowserContext as AsyncBrowserContext, Error as PlaywrightAsyncError, expect as expect_async

# Config and models
from config import (
    AI_STUDIO_URL_PATTERN,
    USER_INPUT_START_MARKER_SERVER,
    USER_INPUT_END_MARKER_SERVER,
    INPUT_SELECTOR,
    AUTO_CONFIRM_LOGIN,
    AUTO_SAVE_AUTH,
    AUTH_SAVE_TIMEOUT,
    SAVED_AUTH_DIR,
)
from models import ClientDisconnectedError

logger = logging.getLogger("AIStudioProxyServer")


async def _setup_network_interception_and_scripts(context: AsyncBrowserContext):
    """Set up network interception and init scripts"""
    try:
        from config.settings import ENABLE_SCRIPT_INJECTION

        if not ENABLE_SCRIPT_INJECTION:
            logger.info("Script injection disabled")
            return

        # Network interception
        await _setup_model_list_interception(context)

        # Optional: still add init scripts as fallback
        await _add_init_scripts_to_context(context)

    except Exception as e:
        logger.error(f"Error setting up network interception and script injection: {e}")


async def _setup_model_list_interception(context: AsyncBrowserContext):
    """Set up model list network interception"""
    try:
        async def handle_model_list_route(route):
            """Handle route for model list request"""
            request = route.request

            # Check if request is model list
            if 'alkalimakersuite' in request.url and 'ListModels' in request.url:
                logger.info(f"🔍 Intercepted model list request: {request.url}")

                # Continue original request
                response = await route.fetch()

                # Get original response body
                original_body = await response.body()

                # Modify response
                modified_body = await _modify_model_list_response(original_body, request.url)

                # Fulfill with modified response
                await route.fulfill(
                    response=response,
                    body=modified_body
                )
            else:
                # Continue other requests
                await route.continue_()

        # Register route interceptor
        await context.route("**/*", handle_model_list_route)
        logger.info("✅ Model list network interception set up")

    except Exception as e:
        logger.error(f"Error setting up model list interception: {e}")


async def _modify_model_list_response(original_body: bytes, url: str) -> bytes:
    """Modify model list response"""
    try:
        # Decode body
        original_text = original_body.decode('utf-8')

        # Handle anti-hijack prefix
        ANTI_HIJACK_PREFIX = ")]}'\n"
        has_prefix = False
        if original_text.startswith(ANTI_HIJACK_PREFIX):
            original_text = original_text[len(ANTI_HIJACK_PREFIX):]
            has_prefix = True

        # Parse JSON
        import json
        json_data = json.loads(original_text)

        # Inject models
        modified_data = await _inject_models_to_response(json_data, url)

        # Serialize back to JSON
        modified_text = json.dumps(modified_data, separators=(',', ':'))

        # Re-add prefix
        if has_prefix:
            modified_text = ANTI_HIJACK_PREFIX + modified_text

        logger.info("✅ Successfully modified model list response")
        return modified_text.encode('utf-8')

    except Exception as e:
        logger.error(f"Error modifying model list response: {e}")
        return original_body


async def _inject_models_to_response(json_data: dict, url: str) -> dict:
    """Inject models into response"""
    try:
        from .operations import _get_injected_models

        # Get models to inject
        injected_models = _get_injected_models()
        if not injected_models:
            logger.info("No models to inject")
            return json_data

        # Find models array
        models_array = _find_model_list_array(json_data)
        if not models_array:
            logger.warning("Model array structure not found")
            return json_data

        # Find template model
        template_model = _find_template_model(models_array)
        if not template_model:
            logger.warning("Template model not found")
            return json_data

        # Inject models
        for model in reversed(injected_models):  # reverse to preserve order
            model_name = model['raw_model_path']

            # Check if model exists
            if not any(m[0] == model_name for m in models_array if isinstance(m, list) and len(m) > 0):
                # Create new model entry
                new_model = json.loads(json.dumps(template_model))  # deep copy
                new_model[0] = model_name  # name
                new_model[3] = model['display_name']  # display name
                new_model[4] = model['description']  # description

                # Add special marker indicating network-injected model
                # Append a field at the end as a marker
                if len(new_model) > 10:  # ensure sufficient length
                    new_model.append("__NETWORK_INJECTED__")
                else:
                    # If not long enough, extend
                    while len(new_model) <= 10:
                        new_model.append(None)
                    new_model.append("__NETWORK_INJECTED__")

                # Insert at beginning
                models_array.insert(0, new_model)
                logger.info(f"✅ Network-injected model: {model['display_name']}")

        return json_data

    except Exception as e:
        logger.error(f"Error injecting models into response: {e}")
        return json_data


def _find_model_list_array(obj):
    """Recursively find model list array"""
    if not obj:
        return None

    # Check model array
    if isinstance(obj, list) and len(obj) > 0:
        if all(isinstance(item, list) and len(item) > 0 and
               isinstance(item[0], str) and item[0].startswith('models/')
               for item in obj):
            return obj

    # Recurse
    if isinstance(obj, dict):
        for value in obj.values():
            result = _find_model_list_array(value)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_model_list_array(item)
            if result:
                return result

    return None


def _find_template_model(models_array):
    """Find template model"""
    if not models_array:
        return None

    # Prefer models whose names contain 'flash' or 'pro'
    for model in models_array:
        if isinstance(model, list) and len(model) > 7:
            model_name = model[0] if len(model) > 0 else ""
            if 'flash' in model_name.lower() or 'pro' in model_name.lower():
                return model

    # Fallback: return first valid model
    for model in models_array:
        if isinstance(model, list) and len(model) > 7:
            return model

    return None


async def _add_init_scripts_to_context(context: AsyncBrowserContext):
    """Add initialization scripts to browser context (fallback)"""
    try:
        from config.settings import USERSCRIPT_PATH

        # Check script exists
        if not os.path.exists(USERSCRIPT_PATH):
            logger.info(f"User script not found; skipping injection: {USERSCRIPT_PATH}")
            return

        # Read script content
        with open(USERSCRIPT_PATH, 'r', encoding='utf-8') as f:
            script_content = f.read()

        # Clean UserScript headers
        cleaned_script = _clean_userscript_headers(script_content)

        # Add to context init scripts
        await context.add_init_script(cleaned_script)
        logger.info(f"✅ Added script to browser context init scripts: {os.path.basename(USERSCRIPT_PATH)}")

    except Exception as e:
        logger.error(f"Error adding init scripts to context: {e}")


def _clean_userscript_headers(script_content: str) -> str:
    """Clean UserScript header block from script"""
    lines = script_content.split('\n')
    cleaned_lines = []
    in_userscript_block = False

    for line in lines:
        if line.strip().startswith('// ==UserScript=='):
            in_userscript_block = True
            continue
        elif line.strip().startswith('// ==/UserScript=='):
            in_userscript_block = False
            continue
        elif in_userscript_block:
            continue
        else:
            cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)


async def _initialize_page_logic(browser: AsyncBrowser):
    """Initialize page logic: connect to existing browser"""
    logger.info("--- Initialize page logic (connect to existing browser) ---")
    temp_context: Optional[AsyncBrowserContext] = None
    storage_state_path_to_use: Optional[str] = None
    launch_mode = os.environ.get('LAUNCH_MODE', 'debug')
    logger.info(f"   Detected launch mode: {launch_mode}")
    loop = asyncio.get_running_loop()

    if launch_mode == 'headless' or launch_mode == 'virtual_headless':
        auth_filename = os.environ.get('ACTIVE_AUTH_JSON_PATH')
        if auth_filename:
            constructed_path = auth_filename
            if os.path.exists(constructed_path):
                storage_state_path_to_use = constructed_path
                logger.info(f"   Headless mode will use auth file: {constructed_path}")
            else:
                logger.error(f"{launch_mode} mode auth file invalid or missing: '{constructed_path}'")
                raise RuntimeError(f"{launch_mode} mode auth file invalid: '{constructed_path}'")
        else:
            logger.error(f"{launch_mode} mode requires ACTIVE_AUTH_JSON_PATH env var but it's missing or empty.")
            raise RuntimeError(f"{launch_mode} mode requires ACTIVE_AUTH_JSON_PATH.")
    elif launch_mode == 'debug':
        logger.info(f"   Debug mode: trying to load auth from env ACTIVE_AUTH_JSON_PATH...")
        auth_filepath_from_env = os.environ.get('ACTIVE_AUTH_JSON_PATH')
        if auth_filepath_from_env and os.path.exists(auth_filepath_from_env):
            storage_state_path_to_use = auth_filepath_from_env
            logger.info(f"   Debug mode will use auth file (from env): {storage_state_path_to_use}")
        elif auth_filepath_from_env:
            logger.warning(f"   Debug mode env ACTIVE_AUTH_JSON_PATH points to a non-existent file: '{auth_filepath_from_env}'. Skipping auth loading.")
        else:
            logger.info("   Debug mode without auth from env; will use browser current state.")
    elif launch_mode == "direct_debug_no_browser":
        logger.info("   direct_debug_no_browser mode: not loading storage_state nor performing browser ops.")
    else:
        logger.warning(f"   ⚠️ Warning: unknown launch mode '{launch_mode}'. Not loading storage_state.")

    try:
        logger.info("Creating new browser context...")
        context_options: Dict[str, Any] = {'viewport': {'width': 460, 'height': 800}}
        if storage_state_path_to_use:
            context_options['storage_state'] = storage_state_path_to_use
            logger.info(f"   (using storage_state='{os.path.basename(storage_state_path_to_use)}')")
        else:
            logger.info("   (not using storage_state)")

        # Proxy settings from server module
        import server
        if server.PLAYWRIGHT_PROXY_SETTINGS:
            context_options['proxy'] = server.PLAYWRIGHT_PROXY_SETTINGS
            logger.info(f"   (browser context will use proxy: {server.PLAYWRIGHT_PROXY_SETTINGS['server']})")
        else:
            logger.info("   (no explicit proxy configuration)")

        context_options['ignore_https_errors'] = True
        logger.info("   (browser context will ignore HTTPS errors)")

        temp_context = await browser.new_context(**context_options)

        # Set up network interception and scripts
        await _setup_network_interception_and_scripts(temp_context)

        found_page: Optional[AsyncPage] = None
        pages = temp_context.pages
        target_url_base = f"https://{AI_STUDIO_URL_PATTERN}"
        target_full_url = f"{target_url_base}prompts/new_chat"
        login_url_pattern = 'accounts.google.com'
        current_url = ""

        # Import _handle_model_list_response lazily to avoid cycles
        from .operations import _handle_model_list_response

        for p_iter in pages:
            try:
                page_url_to_check = p_iter.url
                if not p_iter.is_closed() and target_url_base in page_url_to_check and "/prompts/" in page_url_to_check:
                    found_page = p_iter
                    current_url = page_url_to_check
                    logger.info(f"   Found existing AI Studio page: {current_url}")
                    if found_page:
                        logger.info(f"   Adding model list response listener to existing page {found_page.url}.")
                        found_page.on("response", _handle_model_list_response)
                    break
            except PlaywrightAsyncError as pw_err_url:
                logger.warning(f"   Playwright error while checking page URL: {pw_err_url}")
            except AttributeError as attr_err_url:
                logger.warning(f"   Attribute error while checking page URL: {attr_err_url}")
            except Exception as e_url_check:
                logger.warning(f"   Unexpected error while checking page URL: {e_url_check} (type: {type(e_url_check).__name__})")

        if not found_page:
            logger.info(f"-> No suitable existing page found; opening new page and navigating to {target_full_url}...")
            found_page = await temp_context.new_page()
            if found_page:
                logger.info(f"   Adding model list response listener to new page (before navigation).")
                found_page.on("response", _handle_model_list_response)
            try:
                await found_page.goto(target_full_url, wait_until="domcontentloaded", timeout=90000)
                current_url = found_page.url
                logger.info(f"-> New page navigation attempt complete. Current URL: {current_url}")
            except Exception as new_page_nav_err:
                # Import save_error_snapshot
                from .operations import save_error_snapshot
                await save_error_snapshot("init_new_page_nav_fail")
                error_str = str(new_page_nav_err)
                if "NS_ERROR_NET_INTERRUPT" in error_str:
                    logger.error("\n" + "="*30 + " Network navigation error hint " + "="*30)
                    logger.error(f"❌ Failed to navigate to '{target_full_url}'; network interrupt (NS_ERROR_NET_INTERRUPT).")
                    logger.error("   Usually indicates browser connection dropped unexpectedly while loading.")
                    logger.error("   Possible causes and checks:")
                    logger.error("     1. Network: ensure local connection is stable; try target URL in normal browser.")
                    logger.error("     2. AI Studio service: confirm aistudio.google.com is available.")
                    logger.error("     3. Firewall/Proxy/VPN: check local firewall, antivirus, proxy or VPN settings.")
                    logger.error("     4. Camoufox service: confirm launch_camoufox.py is running.")
                    logger.error("     5. System resources: ensure enough memory and CPU.")
                    logger.error("="*74 + "\n")
                raise RuntimeError(f"Failed to navigate new page: {new_page_nav_err}") from new_page_nav_err

        if login_url_pattern in current_url:
            if launch_mode == 'headless':
                logger.error("Detected redirect to login in headless mode; auth likely invalid. Please update auth file.")
                raise RuntimeError("Headless auth failure; update auth file.")
            else:
                print(f"\n{'='*20} ACTION REQUIRED {'='*20}", flush=True)
                login_prompt = "   Detected possible login. If browser shows login page, complete Google login in the window, then press Enter here to continue..."
                # NEW: If SUPPRESS_LOGIN_WAIT is set, skip waiting for user input.
                if os.environ.get("SUPPRESS_LOGIN_WAIT", "").lower() in ("1", "true", "yes"):
                    logger.info("Detected SUPPRESS_LOGIN_WAIT; skipping user input wait.")
                else:
                    print(USER_INPUT_START_MARKER_SERVER, flush=True)
                    await loop.run_in_executor(None, input, login_prompt)
                    print(USER_INPUT_END_MARKER_SERVER, flush=True)
                logger.info("   Checking login status...")
                try:
                    await found_page.wait_for_url(f"**/{AI_STUDIO_URL_PATTERN}**", timeout=180000)
                    current_url = found_page.url
                    if login_url_pattern in current_url:
                        logger.error("After manual login attempt, page still at login.")
                        raise RuntimeError("Still on login page after manual login attempt.")
                    logger.info("   ✅ Login successful! Please do not operate the browser; wait for further instructions.")

                    # On login success, trigger auth save logic
                    if os.environ.get('AUTO_SAVE_AUTH', 'false').lower() == 'true':
                        await _wait_for_model_list_and_handle_auth_save(temp_context, launch_mode, loop)

                except Exception as wait_login_err:
                    from .operations import save_error_snapshot
                    await save_error_snapshot("init_login_wait_fail")
                    logger.error(f"Error after login prompt while waiting AI Studio URL or saving state: {wait_login_err}", exc_info=True)
                    raise RuntimeError(f"Login prompt: did not detect AI Studio URL: {wait_login_err}") from wait_login_err

        elif target_url_base not in current_url or "/prompts/" not in current_url:
            from .operations import save_error_snapshot
            await save_error_snapshot("init_unexpected_page")
            logger.error(f"Unexpected URL after initial navigation: {current_url}. Expected to contain '{target_url_base}' and '/prompts/'.")
            raise RuntimeError(f"Unexpected page after initial navigation: {current_url}.")

        logger.info(f"-> Confirmed AI Studio dialog page: {current_url}")
        await found_page.bring_to_front()

        try:
            input_wrapper_locator = found_page.locator('ms-prompt-input-wrapper')
            await expect_async(input_wrapper_locator).to_be_visible(timeout=35000)
            await expect_async(found_page.locator(INPUT_SELECTOR)).to_be_visible(timeout=10000)
            logger.info("-> ✅ Core input area visible.")
            
            model_name_locator = found_page.locator('[data-test-id="model-name"]')
            try:
                model_name_on_page = await model_name_locator.first.inner_text(timeout=5000)
                logger.info(f"-> 🤖 Current model detected on page: {model_name_on_page}")
            except PlaywrightAsyncError as e:
                logger.error(f"Error reading model name (model_name_locator): {e}")
                raise

            result_page_instance = found_page
            result_page_ready = True

            # Script injection already performed at context creation; no need to repeat here

            logger.info(f"✅ Page logic initialization succeeded.")
            return result_page_instance, result_page_ready
        except Exception as input_visible_err:
            from .operations import save_error_snapshot
            await save_error_snapshot("init_fail_input_timeout")
            logger.error(f"Page initialization failed: core input area not visible in time. Last URL: {found_page.url}", exc_info=True)
            raise RuntimeError(f"Page initialization failed: core input area not visible in time. Last URL: {found_page.url}") from input_visible_err
    except Exception as e_init_page:
        logger.critical(f"❌ Critical unexpected error during page logic initialization: {e_init_page}", exc_info=True)
        if temp_context:
            try:
                logger.info(f"   Attempting to close temporary browser context due to initialization error.")
                await temp_context.close()
                logger.info("   ✅ Temporary browser context closed.")
            except Exception as close_err:
                 logger.warning(f"   ⚠️ Error closing temporary browser context: {close_err}")
        from .operations import save_error_snapshot
        await save_error_snapshot("init_unexpected_error")
        raise RuntimeError(f"Unexpected error during page initialization: {e_init_page}") from e_init_page


async def _close_page_logic():
    """Close page logic"""
    # Access global variables
    import server
    logger.info("--- Running page logic close --- ")
    if server.page_instance and not server.page_instance.is_closed():
        try:
            await server.page_instance.close()
            logger.info("   ✅ Page closed")
        except PlaywrightAsyncError as pw_err:
            logger.warning(f"   ⚠️ Playwright error while closing page: {pw_err}")
        except asyncio.TimeoutError as timeout_err:
            logger.warning(f"   ⚠️ Timeout while closing page: {timeout_err}")
        except Exception as other_err:
            logger.error(f"   ⚠️ Unexpected error while closing page: {other_err} (type: {type(other_err).__name__})", exc_info=True)
    server.page_instance = None
    server.is_page_ready = False
    logger.info("Page logic state reset.")
    return None, False


async def signal_camoufox_shutdown():
    """Send shutdown signal to Camoufox server"""
    logger.info("   Attempting to send shutdown signal to Camoufox server (may be handled by parent process)...")
    ws_endpoint = os.environ.get('CAMOUFOX_WS_ENDPOINT')
    if not ws_endpoint:
        logger.warning("   ⚠️ Cannot send shutdown signal: CAMOUFOX_WS_ENDPOINT env missing.")
        return

    # Access global browser instance
    import server
    if not server.browser_instance or not server.browser_instance.is_connected():
        logger.warning("   ⚠️ Browser instance disconnected or not initialized; skipping shutdown signal.")
        return
    try:
        await asyncio.sleep(0.2)
        logger.info("   ✅ (Simulated) shutdown signal handled.")
    except Exception as e:
        logger.error(f"   ⚠️ Exception during shutdown signal: {e}", exc_info=True)


async def _wait_for_model_list_and_handle_auth_save(temp_context, launch_mode, loop):
    """Wait for model list response and handle auth save"""
    import server

    # Wait for model list response to confirm login success
    logger.info("   Waiting for model list response to confirm login...")
    try:
        # Wait up to 30s for model list event
        await asyncio.wait_for(server.model_list_fetch_event.wait(), timeout=30.0)
        logger.info("   ✅ Model list response detected; login confirmed!")
    except asyncio.TimeoutError:
        logger.warning("   ⚠️ Timed out waiting for model list response; proceeding to auth save...")

    # Check preset filename for save
    save_auth_filename = os.environ.get('SAVE_AUTH_FILENAME', '').strip()
    if save_auth_filename:
        logger.info(f"   Detected SAVE_AUTH_FILENAME env: '{save_auth_filename}'. Will auto-save auth state.")
        await _handle_auth_file_save_with_filename(temp_context, save_auth_filename)
        return

    # If not auto-saving, proceed with interactive prompts
    await _interactive_auth_save(temp_context, launch_mode, loop)


async def _interactive_auth_save(temp_context, launch_mode, loop):
    """Interactive prompts for saving auth state"""
    # Check auto-confirm
    if AUTO_CONFIRM_LOGIN:
        print("\n" + "="*50, flush=True)
        print("   ✅ Login successful! Model list response detected.", flush=True)
        print("   🤖 Auto-confirm mode enabled; will auto-save auth state...", flush=True)

        # Auto-save
        await _handle_auth_file_save_auto(temp_context)
        print("="*50 + "\n", flush=True)
        return

    # Manual confirmation mode
    print("\n" + "="*50, flush=True)
    print("   [User Interaction] Your input is required!", flush=True)
    print("   ✅ Login successful! Model list response detected.", flush=True)

    should_save_auth_choice = ''
    if AUTO_SAVE_AUTH and launch_mode == 'debug':
        logger.info("   Auto-save auth mode enabled; will auto-save auth state...")
        should_save_auth_choice = 'y'
    else:
        save_auth_prompt = "   Save current browser auth state to file? (y/N): "
        print(USER_INPUT_START_MARKER_SERVER, flush=True)
        try:
            auth_save_input_future = loop.run_in_executor(None, input, save_auth_prompt)
            should_save_auth_choice = await asyncio.wait_for(auth_save_input_future, timeout=AUTH_SAVE_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"   Input timed out ({AUTH_SAVE_TIMEOUT}s). Defaulting to not save auth.", flush=True)
            should_save_auth_choice = 'n'
        finally:
            print(USER_INPUT_END_MARKER_SERVER, flush=True)

    if should_save_auth_choice.strip().lower() == 'y':
        await _handle_auth_file_save(temp_context, loop)
    else:
        print("   Okay, not saving auth state.", flush=True)

    print("="*50 + "\n", flush=True)


async def _handle_auth_file_save(temp_context, loop):
    """Handle saving auth file (manual mode)"""
    os.makedirs(SAVED_AUTH_DIR, exist_ok=True)
    default_auth_filename = f"auth_state_{int(time.time())}.json"

    print(USER_INPUT_START_MARKER_SERVER, flush=True)
    filename_prompt_str = f"   Enter filename to save (default: {default_auth_filename}, type 'cancel' to abort): "
    chosen_auth_filename = ''

    try:
        filename_input_future = loop.run_in_executor(None, input, filename_prompt_str)
        chosen_auth_filename = await asyncio.wait_for(filename_input_future, timeout=AUTH_SAVE_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"   Filename input timed out ({AUTH_SAVE_TIMEOUT}s). Will use default: {default_auth_filename}", flush=True)
        chosen_auth_filename = default_auth_filename
    finally:
        print(USER_INPUT_END_MARKER_SERVER, flush=True)

    if chosen_auth_filename.strip().lower() == 'cancel':
        print("   User chose to cancel saving auth state.", flush=True)
        return

    final_auth_filename = chosen_auth_filename.strip() or default_auth_filename
    if not final_auth_filename.endswith(".json"):
        final_auth_filename += ".json"

    auth_save_path = os.path.join(SAVED_AUTH_DIR, final_auth_filename)

    try:
        await temp_context.storage_state(path=auth_save_path)
        logger.info(f"   Auth state saved to: {auth_save_path}")
        print(f"   ✅ Auth state saved to: {auth_save_path}", flush=True)
    except Exception as save_state_err:
        logger.error(f"   ❌ Failed saving auth state: {save_state_err}", exc_info=True)
        print(f"   ❌ Failed saving auth state: {save_state_err}", flush=True)


async def _handle_auth_file_save_with_filename(temp_context, filename: str):
    """Handle saving auth file (given filename)"""
    os.makedirs(SAVED_AUTH_DIR, exist_ok=True)

    # Clean filename and add .json if needed
    final_auth_filename = filename.strip()
    if not final_auth_filename.endswith(".json"):
        final_auth_filename += ".json"

    auth_save_path = os.path.join(SAVED_AUTH_DIR, final_auth_filename)

    try:
        await temp_context.storage_state(path=auth_save_path)
        print(f"   ✅ Auth state auto-saved to: {auth_save_path}", flush=True)
        logger.info(f"   Auth state auto-save succeeded: {auth_save_path}")
    except Exception as save_state_err:
        logger.error(f"   ❌ Failed auto-saving auth state: {save_state_err}", exc_info=True)
        print(f"   ❌ Failed auto-saving auth state: {save_state_err}", flush=True)


async def _handle_auth_file_save_auto(temp_context):
    """Handle saving auth file (auto mode)"""
    os.makedirs(SAVED_AUTH_DIR, exist_ok=True)

    # Timestamp-based filename
    timestamp = int(time.time())
    auto_auth_filename = f"auth_auto_{timestamp}.json"
    auth_save_path = os.path.join(SAVED_AUTH_DIR, auto_auth_filename)

    try:
        await temp_context.storage_state(path=auth_save_path)
        logger.info(f"   Auth state saved to: {auth_save_path}")
        print(f"   ✅ Auth state saved to: {auth_save_path}", flush=True)
    except Exception as save_state_err:
        logger.error(f"   ❌ Failed auto-saving auth state: {save_state_err}", exc_info=True)
        print(f"   ❌ Failed auto-saving auth state: {save_state_err}", flush=True)

async def enable_temporary_chat_mode(page: AsyncPage):
    """
    Check and enable 'Temporary chat' mode on AI Studio UI.
    This is a standalone UI operation; call after page stabilized.
    """
    try:
        logger.info("-> (UI Op) Checking and enabling 'Temporary chat' mode...")
        
        incognito_button_locator = page.locator('button[aria-label="Temporary chat toggle"]')
        
        await incognito_button_locator.wait_for(state="visible", timeout=10000)
        
        button_classes = await incognito_button_locator.get_attribute("class")
        
        if button_classes and 'ms-button-active' in button_classes:
            logger.info("-> (UI Op) 'Temporary chat' mode is active.")
        else:
            logger.info("-> (UI Op) 'Temporary chat' mode is not active; clicking...")
            await incognito_button_locator.click(timeout=5000, force=True)
            await asyncio.sleep(1)
            
            updated_classes = await incognito_button_locator.get_attribute("class")
            if updated_classes and 'ms-button-active' in updated_classes:
                logger.info("✅ (UI Op) 'Temporary chat' mode enabled successfully.")
            else:
                logger.warning("⚠️ (UI Op) After click, 'Temporary chat' mode state validation failed.")

    except Exception as e:
        logger.warning(f"⚠️ (UI Op) Error enabling 'Temporary chat' mode: {e}")
