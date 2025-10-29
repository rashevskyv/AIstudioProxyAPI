# --- browser_utils/operations.py ---
# Browser page operation utilities

import asyncio
import time
import json
import os
import re
import logging
from typing import Optional, Any, List, Dict, Callable, Set

from playwright.async_api import Page as AsyncPage, Locator, Error as PlaywrightAsyncError

# Config and models
from config import (
    DEBUG_LOGS_ENABLED,
    MODELS_ENDPOINT_URL_CONTAINS,
    ERROR_TOAST_SELECTOR,
    CLICK_TIMEOUT_MS,
    RESPONSE_COMPLETION_TIMEOUT,
    INITIAL_WAIT_MS_BEFORE_POLLING,
    CLEAR_CHAT_BUTTON_SELECTOR,
    CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR,
    OVERLAY_SELECTOR,
    WAIT_FOR_ELEMENT_TIMEOUT_MS,
    SUBMIT_BUTTON_SELECTOR,
    LOADING_SPINNER_SELECTOR,
)
from models import ClientDisconnectedError

logger = logging.getLogger("AIStudioProxyServer")

async def get_raw_text_content(response_element: Locator, previous_text: str, req_id: str) -> str:
    """Get raw text content from response element"""
    raw_text = previous_text
    try:
        await response_element.wait_for(state='attached', timeout=1000)
        pre_element = response_element.locator('pre').last
        pre_found_and_visible = False
        try:
            await pre_element.wait_for(state='visible', timeout=250)
            pre_found_and_visible = True
        except PlaywrightAsyncError: 
            pass
        
        if pre_found_and_visible:
            try:
                raw_text = await pre_element.inner_text(timeout=500)
            except PlaywrightAsyncError as pre_err:
                if DEBUG_LOGS_ENABLED:
                    logger.debug(f"[{req_id}] (GetRawText) Failed to get inner text from pre element: {pre_err}")
        else:
            try:
                raw_text = await response_element.inner_text(timeout=500)
            except PlaywrightAsyncError as e_parent:
                if DEBUG_LOGS_ENABLED:
                    logger.debug(f"[{req_id}] (GetRawText) Failed to get inner text from response element: {e_parent}")
    except PlaywrightAsyncError as e_parent:
        if DEBUG_LOGS_ENABLED:
            logger.debug(f"[{req_id}] (GetRawText) Response element not ready: {e_parent}")
    except Exception as e_unexpected:
        logger.warning(f"[{req_id}] (GetRawText) Unexpected error: {e_unexpected}")
    
    if raw_text != previous_text:
        if DEBUG_LOGS_ENABLED:
            preview = raw_text[:100].replace('\n', '\\n')
            logger.debug(f"[{req_id}] (GetRawText) Text updated, length: {len(raw_text)}, preview: '{preview}...'")
    return raw_text

def _parse_userscript_models(script_content: str):
    """Parse model list from userscript via JSON-like conversion"""
    try:
        version_pattern = r'const\s+SCRIPT_VERSION\s*=\s*[\'\"]([^\'\"]+)[\'\"]'
        version_match = re.search(version_pattern, script_content)
        script_version = version_match.group(1) if version_match else "v1.6"

        models_array_pattern = r'const\s+MODELS_TO_INJECT\s*=\s*(\[.*?\]);'
        models_match = re.search(models_array_pattern, script_content, re.DOTALL)

        if not models_match:
            logger.warning("MODELS_TO_INJECT array not found")
            return []

        models_js_code = models_match.group(1)
        models_js_code = models_js_code.replace('${SCRIPT_VERSION}', script_version)
        models_js_code = re.sub(r'//.*?$', '', models_js_code, flags=re.MULTILINE)
        models_js_code = re.sub(r',\s*([}\]])', r'\1', models_js_code)
        models_js_code = re.sub(r"(\w+):\s*'([^']*)'", r'"\1": "\2"', models_js_code)
        models_js_code = re.sub(r'(\w+):\s*`([^`]*)`', r'"\1": "\2"', models_js_code)
        models_js_code = re.sub(r'(\w+):', r'"\1":', models_js_code)

        import json
        models_data = json.loads(models_js_code)

        models = []
        for model_obj in models_data:
            if isinstance(model_obj, dict) and 'name' in model_obj:
                models.append({
                    'name': model_obj.get('name', ''),
                    'displayName': model_obj.get('displayName', ''),
                    'description': model_obj.get('description', '')
                })

        logger.info(f"Successfully parsed {len(models)} models from userscript")
        return models

    except Exception as e:
        logger.error(f"Failed parsing userscript model list: {e}")
        return []


def _get_injected_models():
    """Get injected models from userscript and convert to API format"""
    try:
        enable_injection = os.environ.get('ENABLE_SCRIPT_INJECTION', 'true').lower() in ('true', '1', 'yes')
        if not enable_injection:
            return []

        script_path = os.environ.get('USERSCRIPT_PATH', 'browser_utils/more_modles.js')
        if not os.path.exists(script_path):
            return []

        with open(script_path, 'r', encoding='utf-8') as f:
            script_content = f.read()

        models = _parse_userscript_models(script_content)
        if not models:
            return []

        injected_models = []
        for model in models:
            model_name = model.get('name', '')
            if not model_name:
                continue

            if model_name.startswith('models/'):
                simple_id = model_name[7:]
            else:
                simple_id = model_name

            display_name = model.get('displayName', model.get('display_name', simple_id))
            description = model.get('description', f'Injected model: {simple_id}')

            model_entry = {
                "id": simple_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ai_studio_injected",
                "display_name": display_name,
                "description": description,
                "raw_model_path": model_name,
                "default_temperature": 1.0,
                "default_max_output_tokens": 65536,
                "supported_max_output_tokens": 65536,
                "default_top_p": 0.95,
                "injected": True
            }
            injected_models.append(model_entry)

        return injected_models

    except Exception:
        return []


async def _handle_model_list_response(response: Any):
    """Handle model list response"""
    import server
    global_model_list_raw_json = getattr(server, 'global_model_list_raw_json', None)
    parsed_model_list = getattr(server, 'parsed_model_list', [])
    model_list_fetch_event = getattr(server, 'model_list_fetch_event', None)
    excluded_model_ids = getattr(server, 'excluded_model_ids', set())
    
    if MODELS_ENDPOINT_URL_CONTAINS in response.url and response.ok:
        launch_mode = os.environ.get('LAUNCH_MODE', 'debug')
        is_in_login_flow = launch_mode in ['debug'] and not getattr(server, 'is_page_ready', False)

        if not is_in_login_flow:
            logger.info(f"Captured potential model list response from: {response.url} (status: {response.status})")
        try:
            data = await response.json()
            models_array_container = None
            if isinstance(data, list) and data:
                if isinstance(data[0], list) and data[0] and isinstance(data[0][0], list):
                    if not is_in_login_flow:
                        logger.info("Detected triple-nested list: data[0][0] is list. Setting models_array_container = data[0].")
                    models_array_container = data[0]
                elif isinstance(data[0], list) and data[0] and isinstance(data[0][0], str):
                    if not is_in_login_flow:
                        logger.info("Detected double-nested list: data[0][0] is str. Setting models_array_container = data.")
                    models_array_container = data
                elif isinstance(data[0], dict):
                    if not is_in_login_flow:
                        logger.info("Detected top-level list of dicts. Using data as models_array_container.")
                    models_array_container = data
                else:
                    logger.warning(f"Unknown list nesting. data[0] type: {type(data[0]) if data else 'N/A'}. Preview of data[0]: {str(data[0])[:200] if data else 'N/A'}")
            elif isinstance(data, dict):
                if 'data' in data and isinstance(data['data'], list):
                    models_array_container = data['data']
                elif 'models' in data and isinstance(data['models'], list):
                    models_array_container = data['models']
                else:
                    for key, value in data.items():
                        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], (dict, list)):
                            models_array_container = value
                            logger.info(f"Model list array heuristically found under key '{key}'.")
                            break
                    if models_array_container is None:
                        logger.warning("Could not automatically locate model list array in dict response.")
                        if model_list_fetch_event and not model_list_fetch_event.is_set(): 
                            model_list_fetch_event.set()
                        return
            else:
                logger.warning(f"Model list response is neither list nor dict: {type(data)}")
                if model_list_fetch_event and not model_list_fetch_event.is_set(): 
                    model_list_fetch_event.set()
                return
            
            if models_array_container is not None:
                new_parsed_list = []
                for entry_in_container in models_array_container:
                    model_fields_list = None
                    if isinstance(entry_in_container, dict):
                        potential_id = entry_in_container.get('id', entry_in_container.get('model_id', entry_in_container.get('modelId')))
                        if potential_id: 
                            model_fields_list = entry_in_container
                        else: 
                            model_fields_list = list(entry_in_container.values())
                    elif isinstance(entry_in_container, list):
                        model_fields_list = entry_in_container
                    else:
                        logger.debug(f"Skipping entry of unknown type: {type(entry_in_container)}")
                        continue
                    
                    if not model_fields_list:
                        logger.debug("Skipping entry because model_fields_list is empty or None.")
                        continue
                    
                    model_id_path_str = None
                    display_name_candidate = ""
                    description_candidate = "N/A"
                    default_max_output_tokens_val = None
                    default_top_p_val = None
                    default_temperature_val = 1.0
                    supported_max_output_tokens_val = None
                    current_model_id_for_log = "UnknownModelYet"
                    
                    try:
                        if isinstance(model_fields_list, list):
                            if not (len(model_fields_list) > 0 and isinstance(model_fields_list[0], (str, int, float))):
                                logger.debug(f"Skipping list-based model_fields due to invalid first element: {str(model_fields_list)[:100]}")
                                continue
                            model_id_path_str = str(model_fields_list[0])
                            current_model_id_for_log = model_id_path_str.split('/')[-1] if model_id_path_str and '/' in model_id_path_str else model_id_path_str
                            display_name_candidate = str(model_fields_list[3]) if len(model_fields_list) > 3 else ""
                            description_candidate = str(model_fields_list[4]) if len(model_fields_list) > 4 else "N/A"
                            
                            if len(model_fields_list) > 6 and model_fields_list[6] is not None:
                                try:
                                    val_int = int(model_fields_list[6])
                                    default_max_output_tokens_val = val_int
                                    supported_max_output_tokens_val = val_int
                                except (ValueError, TypeError):
                                    logger.warning(f"Model {current_model_id_for_log}: Cannot parse list index 6 value '{model_fields_list[6]}' as max_output_tokens.")
                            
                            if len(model_fields_list) > 9 and model_fields_list[9] is not None:
                                try:
                                    raw_top_p = float(model_fields_list[9])
                                    if not (0.0 <= raw_top_p <= 1.0):
                                        logger.warning(f"Model {current_model_id_for_log}: Raw top_p {raw_top_p} (from list index 9) out of [0,1], will clamp.")
                                        default_top_p_val = max(0.0, min(1.0, raw_top_p))
                                    else:
                                        default_top_p_val = raw_top_p
                                except (ValueError, TypeError):
                                    logger.warning(f"Model {current_model_id_for_log}: Cannot parse list index 9 value '{model_fields_list[9]}' as top_p.")
                                    
                        elif isinstance(model_fields_list, dict):
                            model_id_path_str = str(model_fields_list.get('id', model_fields_list.get('model_id', model_fields_list.get('modelId'))))
                            current_model_id_for_log = model_id_path_str.split('/')[-1] if model_id_path_str and '/' in model_id_path_str else model_id_path_str
                            display_name_candidate = str(model_fields_list.get('displayName', model_fields_list.get('display_name', model_fields_list.get('name', ''))))
                            description_candidate = str(model_fields_list.get('description', "N/A"))
                            
                            mot_parsed = model_fields_list.get('maxOutputTokens', model_fields_list.get('defaultMaxOutputTokens', model_fields_list.get('outputTokenLimit')))
                            if mot_parsed is not None:
                                try:
                                    val_int = int(mot_parsed)
                                    default_max_output_tokens_val = val_int
                                    supported_max_output_tokens_val = val_int
                                except (ValueError, TypeError):
                                     logger.warning(f"Model {current_model_id_for_log}: Cannot parse dict value '{mot_parsed}' as max_output_tokens.")
                            
                            top_p_parsed = model_fields_list.get('topP', model_fields_list.get('defaultTopP'))
                            if top_p_parsed is not None:
                                try:
                                    raw_top_p = float(top_p_parsed)
                                    if not (0.0 <= raw_top_p <= 1.0):
                                        logger.warning(f"Model {current_model_id_for_log}: Raw top_p {raw_top_p} (from dict) out of [0,1], will clamp.")
                                        default_top_p_val = max(0.0, min(1.0, raw_top_p))
                                    else:
                                        default_top_p_val = raw_top_p
                                except (ValueError, TypeError):
                                    logger.warning(f"Model {current_model_id_for_log}: Cannot parse dict value '{top_p_parsed}' as top_p.")
                            
                            temp_parsed = model_fields_list.get('temperature', model_fields_list.get('defaultTemperature'))
                            if temp_parsed is not None:
                                try: 
                                    default_temperature_val = float(temp_parsed)
                                except (ValueError, TypeError):
                                    logger.warning(f"Model {current_model_id_for_log}: Cannot parse dict value '{temp_parsed}' as temperature.")
                        else:
                            logger.debug(f"Skipping entry because model_fields_list is not list or dict: {type(model_fields_list)}")
                            continue
                    except Exception as e_parse_fields:
                        logger.error(f"Error parsing model fields for entry {str(entry_in_container)[:100]}: {e_parse_fields}")
                        continue
                    
                    if model_id_path_str and model_id_path_str.lower() != "none":
                        simple_model_id_str = model_id_path_str.split('/')[-1] if '/' in model_id_path_str else model_id_path_str
                        if simple_model_id_str in excluded_model_ids:
                            if not is_in_login_flow:
                                logger.info(f"Model '{simple_model_id_str}' is in excluded_model_ids; skipping.")
                            continue
                        
                        final_display_name_str = display_name_candidate if display_name_candidate else simple_model_id_str.replace("-", " ").title()
                        model_entry_dict = {
                            "id": simple_model_id_str, 
                            "object": "model", 
                            "created": int(time.time()),
                            "owned_by": "ai_studio", 
                            "display_name": final_display_name_str,
                            "description": description_candidate, 
                            "raw_model_path": model_id_path_str,
                            "default_temperature": default_temperature_val,
                            "default_max_output_tokens": default_max_output_tokens_val,
                            "supported_max_output_tokens": supported_max_output_tokens_val,
                            "default_top_p": default_top_p_val
                        }
                        new_parsed_list.append(model_entry_dict)
                    else:
                        logger.debug(f"Skipping entry due to invalid model_id_path: {model_id_path_str} from entry {str(entry_in_container)[:100]}")
                
                if new_parsed_list:
                    has_network_injected_models = False
                    if models_array_container:
                        for entry_in_container in models_array_container:
                            if isinstance(entry_in_container, list) and len(entry_in_container) > 10:
                                if "__NETWORK_INJECTED__" in entry_in_container:
                                    has_network_injected_models = True
                                    break

                    if has_network_injected_models and not is_in_login_flow:
                        logger.info("Detected network-injected models")

                    server.parsed_model_list = sorted(new_parsed_list, key=lambda m: m.get('display_name', '').lower())
                    server.global_model_list_raw_json = json.dumps({"data": server.parsed_model_list, "object": "list"})
                    if DEBUG_LOGS_ENABLED:
                        log_output = f"Successfully parsed and updated model list. Total: {len(server.parsed_model_list)}.\n"
                        for i, item in enumerate(server.parsed_model_list[:min(3, len(server.parsed_model_list))]):
                            log_output += f"  Model {i+1}: ID={item.get('id')}, Name={item.get('display_name')}, Temp={item.get('default_temperature')}, MaxTokDef={item.get('default_max_output_tokens')}, MaxTokSup={item.get('supported_max_output_tokens')}, TopP={item.get('default_top_p')}\n"
                        logger.info(log_output)
                    if model_list_fetch_event and not model_list_fetch_event.is_set():
                        model_list_fetch_event.set()
                elif not server.parsed_model_list:
                    logger.warning("Parsed model list still empty.")
                    if model_list_fetch_event and not model_list_fetch_event.is_set(): 
                        model_list_fetch_event.set()
            else:
                logger.warning("models_array_container is None; cannot parse model list.")
                if model_list_fetch_event and not model_list_fetch_event.is_set(): 
                    model_list_fetch_event.set()
        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to parse model list JSON: {json_err}. Response (first 500 chars): {await response.text()[:500]}")
        except Exception as e_handle_list_resp:
            logger.exception(f"Unknown error while handling model list response: {e_handle_list_resp}")
        finally:
            if model_list_fetch_event and not model_list_fetch_event.is_set():
                logger.info("Model list response handling finished; force-setting model_list_fetch_event.")
                model_list_fetch_event.set()

async def detect_and_extract_page_error(page: AsyncPage, req_id: str) -> Optional[str]:
    """Detect and extract page error"""
    error_toast_locator = page.locator(ERROR_TOAST_SELECTOR).last
    try:
        await error_toast_locator.wait_for(state='visible', timeout=500)
        message_locator = error_toast_locator.locator('span.content-text')
        error_message = await message_locator.text_content(timeout=500)
        if error_message:
             logger.error(f"[{req_id}]    Detected and extracted error message: {error_message}")
             return error_message.strip()
        else:
             logger.warning(f"[{req_id}]    Detected error toast but could not extract message.")
             return "Detected error toast but no specific message extracted."
    except PlaywrightAsyncError: 
        return None
    except Exception as e:
        logger.warning(f"[{req_id}]    Error while checking page error: {e}")
        return None

async def save_error_snapshot(error_name: str = 'error'):
    """Save error snapshot"""
    import server
    name_parts = error_name.split('_')
    req_id = name_parts[-1] if len(name_parts) > 1 and len(name_parts[-1]) == 7 else None
    base_error_name = error_name if not req_id else '_'.join(name_parts[:-1])
    log_prefix = f"[{req_id}]" if req_id else "[NoReqID]"
    page_to_snapshot = server.page_instance
    
    if not server.browser_instance or not server.browser_instance.is_connected() or not page_to_snapshot or page_to_snapshot.is_closed():
        logger.warning(f"{log_prefix} Cannot save snapshot ({base_error_name}); browser/page unavailable.")
        return
    
    logger.info(f"{log_prefix} Attempting to save error snapshot ({base_error_name})...")
    timestamp = int(time.time() * 1000)
    error_dir = os.path.join(os.path.dirname(__file__), '..', 'errors_py')
    
    try:
        os.makedirs(error_dir, exist_ok=True)
        filename_suffix = f"{req_id}_{timestamp}" if req_id else f"{timestamp}"
        filename_base = f"{base_error_name}_{filename_suffix}"
        screenshot_path = os.path.join(error_dir, f"{filename_base}.png")
        html_path = os.path.join(error_dir, f"{filename_base}.html")
        
        try:
            await page_to_snapshot.screenshot(path=screenshot_path, full_page=True, timeout=15000)
            logger.info(f"{log_prefix}   Snapshot saved to: {screenshot_path}")
        except Exception as ss_err:
            logger.error(f"{log_prefix}   Failed to save screenshot ({base_error_name}): {ss_err}")
        
        try:
            content = await page_to_snapshot.content()
            f = None
            try:
                f = open(html_path, 'w', encoding='utf-8')
                f.write(content)
                logger.info(f"{log_prefix}   HTML saved to: {html_path}")
            except Exception as write_err:
                logger.error(f"{log_prefix}   Failed to save HTML ({base_error_name}): {write_err}")
            finally:
                if f:
                    try:
                        f.close()
                        logger.debug(f"{log_prefix}   HTML file closed properly")
                    except Exception as close_err:
                        logger.error(f"{log_prefix}   Error while closing HTML file: {close_err}")
        except Exception as html_err:
            logger.error(f"{log_prefix}   Failed to get page content ({base_error_name}): {html_err}")
    except Exception as dir_err:
        logger.error(f"{log_prefix}   Other error while creating error directory or saving snapshot ({base_error_name}): {dir_err}")

async def get_response_via_edit_button(
    page: AsyncPage,
    req_id: str,
    check_client_disconnected: Callable
) -> Optional[str]:
    """Get response via Edit button"""
    logger.info(f"[{req_id}] (Helper) Attempting to get response via Edit button...")
    last_message_container = page.locator('ms-chat-turn').last
    edit_button = last_message_container.get_by_label("Edit")
    finish_edit_button = last_message_container.get_by_label("Stop editing")
    autosize_textarea_locator = last_message_container.locator('ms-autosize-textarea')
    actual_textarea_locator = autosize_textarea_locator.locator('textarea')
    
    try:
        logger.info(f"[{req_id}]   - Hover last message to show 'Edit' button...")
        try:
            await last_message_container.hover(timeout=CLICK_TIMEOUT_MS / 2)
            await asyncio.sleep(0.3)
            check_client_disconnected("Edit response - after hover: ")
        except Exception as hover_err:
            logger.warning(f"[{req_id}]   - (get_response_via_edit_button) Hover last message failed (ignored): {type(hover_err).__name__}")
        
        logger.info(f"[{req_id}]   - Locate and click 'Edit' button...")
        try:
            from playwright.async_api import expect as expect_async
            await expect_async(edit_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("Edit response - after 'Edit' visible: ")
            await edit_button.click(timeout=CLICK_TIMEOUT_MS)
            logger.info(f"[{req_id}]   - 'Edit' clicked.")
        except Exception as edit_btn_err:
            logger.error(f"[{req_id}]   - 'Edit' not visible or click failed: {edit_btn_err}")
            await save_error_snapshot(f"edit_response_edit_button_failed_{req_id}")
            return None
        
        check_client_disconnected("Edit response - after 'Edit' click: ")
        await asyncio.sleep(0.3)
        check_client_disconnected("Edit response - after 'Edit' click delay: ")
        
        logger.info(f"[{req_id}]   - Get content from textarea...")
        response_content = None
        textarea_failed = False
        
        try:
            await expect_async(autosize_textarea_locator).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("Edit response - after autosize-textarea visible: ")
            
            try:
                data_value_content = await autosize_textarea_locator.get_attribute("data-value")
                check_client_disconnected("Edit response - after get_attribute data-value: ")
                if data_value_content is not None:
                    response_content = str(data_value_content)
                    logger.info(f"[{req_id}]   - Content from data-value succeeded.")
            except Exception as data_val_err:
                logger.warning(f"[{req_id}]   - Failed to get data-value: {data_val_err}")
                check_client_disconnected("Edit response - after get_attribute data-value error: ")
            
            if response_content is None:
                logger.info(f"[{req_id}]   - data-value not available, try textarea input_value...")
                try:
                    await expect_async(actual_textarea_locator).to_be_visible(timeout=CLICK_TIMEOUT_MS/2)
                    input_val_content = await actual_textarea_locator.input_value(timeout=CLICK_TIMEOUT_MS/2)
                    check_client_disconnected("Edit response - after input_value: ")
                    if input_val_content is not None:
                        response_content = str(input_val_content)
                        logger.info(f"[{req_id}]   - Content from input_value succeeded.")
                except Exception as input_val_err:
                     logger.warning(f"[{req_id}]   - Getting input_value failed: {input_val_err}")
                     check_client_disconnected("Edit response - after input_value error: ")
            
            if response_content is not None:
                response_content = response_content.strip()
                content_preview = response_content[:100].replace('\\n', '\\\\n')
                logger.info(f"[{req_id}]   - ✅ Final content (length={len(response_content)}): '{content_preview}...'")
            else:
                logger.warning(f"[{req_id}]   - Both data-value and input_value failed or returned None.")
                textarea_failed = True
                
        except Exception as textarea_err:
            logger.error(f"[{req_id}]   - Failed locating/handling textarea: {textarea_err}")
            textarea_failed = True
            response_content = None
            check_client_disconnected("Edit response - after textarea error: ")
        
        if not textarea_failed:
            logger.info(f"[{req_id}]   - Locate and click 'Stop editing' button...")
            try:
                await expect_async(finish_edit_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
                check_client_disconnected("Edit response - after 'Stop editing' visible: ")
                await finish_edit_button.click(timeout=CLICK_TIMEOUT_MS)
                logger.info(f"[{req_id}]   - 'Stop editing' clicked.")
            except Exception as finish_btn_err:
                logger.warning(f"[{req_id}]   - 'Stop editing' not visible or click failed: {finish_btn_err}")
                await save_error_snapshot(f"edit_response_finish_button_failed_{req_id}")
            check_client_disconnected("Edit response - after 'Stop editing' click: ")
            await asyncio.sleep(0.2)
            check_client_disconnected("Edit response - after 'Stop editing' delay: ")
        else:
             logger.info(f"[{req_id}]   - Skipping 'Stop editing' click because textarea read failed.")
        
        return response_content
        
    except ClientDisconnectedError:
        logger.info(f"[{req_id}] (Helper Edit) Client disconnected.")
        raise
    except Exception as e:
        logger.exception(f"[{req_id}] Unexpected error while getting response via Edit button")
        await save_error_snapshot(f"edit_response_unexpected_error_{req_id}")
        return None

async def get_response_via_copy_button(
    page: AsyncPage,
    req_id: str,
    check_client_disconnected: Callable
) -> Optional[str]:
    """Get response via Copy button"""
    logger.info(f"[{req_id}] (Helper) Attempting to get response via Copy button...")
    last_message_container = page.locator('ms-chat-turn').last
    more_options_button = last_message_container.get_by_label("Open options")
    copy_markdown_button = page.get_by_role("menuitem", name="Copy markdown")
    
    try:
        logger.info(f"[{req_id}]   - Hover last message to show options...")
        await last_message_container.hover(timeout=CLICK_TIMEOUT_MS)
        check_client_disconnected("Copy response - after hover: ")
        await asyncio.sleep(0.5)
        check_client_disconnected("Copy response - after hover delay: ")
        logger.info(f"[{req_id}]   - Hovered.")
        
        logger.info(f"[{req_id}]   - Locate and click 'More options' button...")
        try:
            from playwright.async_api import expect as expect_async
            await expect_async(more_options_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("Copy response - after More options visible: ")
            await more_options_button.click(timeout=CLICK_TIMEOUT_MS)
            logger.info(f"[{req_id}]   - 'More options' clicked (get_by_label).")
        except Exception as more_opts_err:
            logger.error(f"[{req_id}]   - 'More options' (get_by_label) not visible or click failed: {more_opts_err}")
            await save_error_snapshot(f"copy_response_more_options_failed_{req_id}")
            return None
        
        check_client_disconnected("Copy response - after More options click: ")
        await asyncio.sleep(0.5)
        check_client_disconnected("Copy response - after More options delay: ")
        
        logger.info(f"[{req_id}]   - Locate and click 'Copy Markdown' button...")
        copy_success = False
        try:
            await expect_async(copy_markdown_button).to_be_visible(timeout=CLICK_TIMEOUT_MS)
            check_client_disconnected("Copy response - after copy button visible: ")
            await copy_markdown_button.click(timeout=CLICK_TIMEOUT_MS, force=True)
            copy_success = True
            logger.info(f"[{req_id}]   - 'Copy Markdown' clicked (get_by_role).")
        except Exception as copy_err:
            logger.error(f"[{req_id}]   - 'Copy Markdown' (get_by_role) click failed: {copy_err}")
            await save_error_snapshot(f"copy_response_copy_button_failed_{req_id}")
            return None
        
        if not copy_success:
             logger.error(f"[{req_id}]   - Could not click 'Copy Markdown' button.")
             return None
             
        check_client_disconnected("Copy response - after copy click: ")
        await asyncio.sleep(0.5)
        check_client_disconnected("Copy response - after copy delay: ")
        
        logger.info(f"[{req_id}]   - Reading clipboard content...")
        try:
            clipboard_content = await page.evaluate('navigator.clipboard.readText()')
            check_client_disconnected("Copy response - after clipboard read: ")
            if clipboard_content:
                content_preview = clipboard_content[:100].replace('\n', '\\\\n')
                logger.info(f"[{req_id}]   - ✅ Successfully got clipboard content (length={len(clipboard_content)}): '{content_preview}...'")
                return clipboard_content
            else:
                logger.error(f"[{req_id}]   - Clipboard content is empty.")
                return None
        except Exception as clipboard_err:
            if "clipboard-read" in str(clipboard_err):
                 logger.error(f"[{req_id}]   - Clipboard read failed: possibly permission issue. Error: {clipboard_err}")
            else:
                 logger.error(f"[{req_id}]   - Clipboard read failed: {clipboard_err}")
            await save_error_snapshot(f"copy_response_clipboard_read_failed_{req_id}")
            return None
            
    except ClientDisconnectedError:
        logger.info(f"[{req_id}] (Helper Copy) Client disconnected.")
        raise
    except Exception as e:
        logger.exception(f"[{req_id}] Unexpected error while getting response via Copy button")
        await save_error_snapshot(f"copy_response_unexpected_error_{req_id}")
        return None

async def _wait_for_response_completion(
    page: AsyncPage,
    prompt_textarea_locator: Locator,
    submit_button_locator: Locator,
    edit_button_locator: Locator,
    req_id: str,
    check_client_disconnected_func: Callable,
    current_chat_id: Optional[str],
    timeout_ms=RESPONSE_COMPLETION_TIMEOUT,
    initial_wait_ms=INITIAL_WAIT_MS_BEFORE_POLLING
) -> bool:
    """Wait for response completion"""
    from playwright.async_api import TimeoutError
    
    logger.info(f"[{req_id}] (WaitV3) Start waiting for response completion... (timeout: {timeout_ms}ms)")
    await asyncio.sleep(initial_wait_ms / 1000)
    
    start_time = time.time()
    wait_timeout_ms_short = 3000
    
    consecutive_empty_input_submit_disabled_count = 0
    
    while True:
        try:
            check_client_disconnected_func("Wait for completion - loop start")
        except ClientDisconnectedError:
            logger.info(f"[{req_id}] (WaitV3) Client disconnected, abort waiting.")
            return False

        current_time_elapsed_ms = (time.time() - start_time) * 1000
        if current_time_elapsed_ms > timeout_ms:
            logger.error(f"[{req_id}] (WaitV3) Timeout waiting for response completion ({timeout_ms}ms).")
            await save_error_snapshot(f"wait_completion_v3_overall_timeout_{req_id}")
            return False

        try:
            check_client_disconnected_func("Wait for completion - after timeout check")
        except ClientDisconnectedError:
            return False

        # Main condition: input empty & submit disabled
        is_input_empty = await prompt_textarea_locator.input_value() == ""
        is_submit_disabled = False
        try:
            is_submit_disabled = await submit_button_locator.is_disabled(timeout=wait_timeout_ms_short)
        except TimeoutError:
            logger.warning(f"[{req_id}] (WaitV3) Timeout checking submit button disabled; assume not disabled for this check.")
        
        try:
            check_client_disconnected_func("Wait for completion - after button check")
        except ClientDisconnectedError:
            return False

        if is_input_empty and is_submit_disabled:
            consecutive_empty_input_submit_disabled_count += 1
            if DEBUG_LOGS_ENABLED:
                logger.debug(f"[{req_id}] (WaitV3) Main condition met: input empty, submit disabled (count: {consecutive_empty_input_submit_disabled_count}).")

            # Final confirmation: edit button visible
            try:
                if await edit_button_locator.is_visible(timeout=wait_timeout_ms_short):
                    logger.info(f"[{req_id}] (WaitV3) ✅ Response completed: input empty, submit disabled, edit button visible.")
                    return True
            except TimeoutError:
                if DEBUG_LOGS_ENABLED:
                    logger.debug(f"[{req_id}] (WaitV3) Timeout checking edit button visibility after main condition.")
            
            try:
                check_client_disconnected_func("Wait for completion - after edit button check")
            except ClientDisconnectedError:
                return False

            # Heuristic completion
            if consecutive_empty_input_submit_disabled_count >= 3:
                logger.warning(f"[{req_id}] (WaitV3) Response likely completed (heuristic): input empty, submit disabled, but edit button did not appear after {consecutive_empty_input_submit_disabled_count} checks.")
                return True
        else:
            consecutive_empty_input_submit_disabled_count = 0
            if DEBUG_LOGS_ENABLED:
                reasons = []
                if not is_input_empty: 
                    reasons.append("input not empty")
                if not is_submit_disabled: 
                    reasons.append("submit not disabled")
                logger.debug(f"[{req_id}] (WaitV3) Main condition not met ({', '.join(reasons)}). Continue polling...")

        await asyncio.sleep(0.5)

async def _get_final_response_content(
    page: AsyncPage,
    req_id: str,
    check_client_disconnected: Callable
) -> Optional[str]:
    """Get final response content"""
    logger.info(f"[{req_id}] (Helper GetContent) Start getting final response content...")
    response_content = await get_response_via_edit_button(
        page, req_id, check_client_disconnected
    )
    if response_content is not None:
        logger.info(f"[{req_id}] (Helper GetContent) ✅ Successfully got content via Edit button.")
        return response_content
    
    logger.warning(f"[{req_id}] (Helper GetContent) Edit button method failed/empty; fallback to Copy button method...")
    response_content = await get_response_via_copy_button(
        page, req_id, check_client_disconnected
    )
    if response_content is not None:
        logger.info(f"[{req_id}] (Helper GetContent) ✅ Successfully got content via Copy button.")
        return response_content
    
    logger.error(f"[{req_id}] (Helper GetContent) All methods to get response content failed.")
    await save_error_snapshot(f"get_content_all_methods_failed_{req_id}")
    return None 

async def create_new_chat(page: AsyncPage, req_id: str) -> bool:
    """
    Create a new chat on AI Studio page.
    Logic: click "New chat" and confirm dialog "Discard and continue" if present.
    If overlay already present, confirm directly.
    Returns True on success; False otherwise.
    """
    logger.info(f"[{req_id}] ACTION: Attempting to create a new chat...")
    try:
        clear_chat_button = page.locator(CLEAR_CHAT_BUTTON_SELECTOR)
        confirm_button = page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)
        overlay_locator = page.locator(OVERLAY_SELECTOR)

        overlay_visible = False
        try:
            overlay_visible = await overlay_locator.is_visible(timeout=500)
        except Exception:
            overlay_visible = False

        if overlay_visible:
            logger.info(f"[{req_id}] Confirm overlay present; clicking confirm button...")
            await confirm_button.click(timeout=CLICK_TIMEOUT_MS)
        else:
            logger.info(f"[{req_id}] Clicking 'New chat' button...")
            try:
                await clear_chat_button.click(timeout=CLICK_TIMEOUT_MS)
            except Exception as first_click_err:
                logger.warning(f"[{req_id}] First click on 'New chat' failed; attempting to clear overlay and force click: {first_click_err}")
                try:
                    await page.keyboard.press('Escape')
                    await asyncio.sleep(0.2)
                except Exception:
                    pass
                await clear_chat_button.click(timeout=CLICK_TIMEOUT_MS, force=True)

            try:
                await overlay_locator.wait_for(state='visible', timeout=WAIT_FOR_ELEMENT_TIMEOUT_MS)
                logger.info(f"[{req_id}] New chat confirmation overlay appeared; clicking confirm...")
            except Exception as overlay_err:
                logger.error(f"[{req_id}] Timeout/failure waiting for new chat confirmation overlay: {overlay_err}")
                await save_error_snapshot(f"new_chat_overlay_timeout_{req_id}")
                return False

            await confirm_button.click(timeout=CLICK_TIMEOUT_MS)

        try:
            await overlay_locator.wait_for(state='hidden', timeout=3000)
        except Exception:
            pass

        try:
            url = page.url.rstrip('/')
            if 'new_chat' in url:
                logger.info(f"[{req_id}] ACTION-SUCCESS: Entered new chat page: {url}")
        except Exception:
            pass

        return True
    except Exception:
        logger.exception(f"[{req_id}] ACTION-FAIL: Error while creating new chat")
        try:
            await save_error_snapshot(f"new_chat_error_{req_id}")
        except Exception:
            pass
        return False

async def click_run_button(page: AsyncPage, req_id: str, delay_ms: int = 0) -> bool:
    """Click the Run button optionally after a delay; auto-handle overlay confirmation and enable state."""
    try:
        submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)
        overlay_locator = page.locator(OVERLAY_SELECTOR)
        confirm_button = page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)

        if delay_ms and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        try:
            if await overlay_locator.count() > 0:
                await confirm_button.click(timeout=CLICK_TIMEOUT_MS)
                try:
                    await overlay_locator.wait_for(state='hidden', timeout=3000)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            await submit_button.wait_for(state='visible', timeout=3000)
        except Exception:
            pass
        try:
            if await submit_button.is_enabled(timeout=1000):
                await submit_button.click(timeout=CLICK_TIMEOUT_MS)
                logger.info(f"[{req_id}] ✅ Run button clicked.")
                return True
        except Exception as click_err:
            logger.warning(f"[{req_id}] ⚠️ Run click failed: {click_err}")
            return False

        logger.info(f"[{req_id}] Run button not enabled; skipping click.")
        return False
    except Exception as e:
        logger.error(f"[{req_id}] ❌ Error in click_run_button: {e}")
        return False

async def click_stop_button(page: AsyncPage, req_id: str, delay_ms: int = 0) -> bool:
    """Click the Stop (toggle Run) button to halt generation; waits briefly for spinner to appear if necessary."""
    try:
        submit_button = page.locator(SUBMIT_BUTTON_SELECTOR)
        overlay_locator = page.locator(OVERLAY_SELECTOR)
        confirm_button = page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)
        spinner_locator = page.locator(LOADING_SPINNER_SELECTOR)

        if delay_ms and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        try:
            if await overlay_locator.count() > 0:
                await confirm_button.click(timeout=CLICK_TIMEOUT_MS)
                try:
                    await overlay_locator.wait_for(state='hidden', timeout=3000)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            try:
                await spinner_locator.first.wait_for(state='visible', timeout=1500)
            except Exception:
                pass
            await submit_button.wait_for(state='visible', timeout=3000)
            if await submit_button.is_enabled(timeout=1000):
                await submit_button.click(timeout=CLICK_TIMEOUT_MS)
                logger.info(f"[{req_id}] ✅ Stop button clicked (Run toggled).")
                return True
            else:
                logger.info(f"[{req_id}] Stop/Run button not enabled; skipping click.")
                return False
        except Exception as click_err:
            logger.warning(f"[{req_id}] ⚠️ Stop click failed: {click_err}")
            return False
    except Exception as e:
        logger.error(f"[{req_id}] ❌ Error in click_stop_button: {e}")
        return False
