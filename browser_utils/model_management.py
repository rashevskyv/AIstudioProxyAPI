# --- browser_utils/model_management.py ---
# Browser model management utilities

import asyncio
import json
import os
import logging
import time
from typing import Optional, Set

from playwright.async_api import Page as AsyncPage, expect as expect_async, Error as PlaywrightAsyncError

# Config and models
from config import (
    INPUT_SELECTOR,
    AI_STUDIO_URL_PATTERN,
)
from models import ClientDisconnectedError

logger = logging.getLogger("AIStudioProxyServer")

# ==================== Forced UI state settings ====================

async def _verify_ui_state_settings(page: AsyncPage, req_id: str = "unknown") -> dict:
    """
    Verify UI state settings in localStorage

    Args:
        page: Playwright page instance
        req_id: request ID for logging

    Returns:
        dict: verification result
    """
    try:
        logger.info(f"[{req_id}] Validating UI state settings...")

        # Get current localStorage settings
        prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")

        if not prefs_str:
            logger.warning(f"[{req_id}] localStorage.aiStudioUserPreference not found")
            return {
                'exists': False,
                'isAdvancedOpen': None,
                'areToolsOpen': None,
                'needsUpdate': True,
                'error': 'localStorage missing'
            }

        try:
            prefs = json.loads(prefs_str)
            is_advanced_open = prefs.get('isAdvancedOpen')
            are_tools_open = prefs.get('areToolsOpen')

            # Check if update needed
            needs_update = (is_advanced_open is not True) or (are_tools_open is not True)

            result = {
                'exists': True,
                'isAdvancedOpen': is_advanced_open,
                'areToolsOpen': are_tools_open,
                'needsUpdate': needs_update,
                'prefs': prefs
            }

            logger.info(f"[{req_id}] UI state verification: isAdvancedOpen={is_advanced_open}, areToolsOpen={are_tools_open} (expected: True), needsUpdate={needs_update}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[{req_id}] Failed to parse localStorage JSON: {e}")
            return {
                'exists': False,
                'isAdvancedOpen': None,
                'areToolsOpen': None,
                'needsUpdate': True,
                'error': f'JSON parse failed: {e}'
            }

    except Exception as e:
        logger.error(f"[{req_id}] Error validating UI state settings: {e}")
        return {
            'exists': False,
            'isAdvancedOpen': None,
            'areToolsOpen': None,
            'needsUpdate': True,
            'error': f'Validation failed: {e}'
        }

async def _force_ui_state_settings(page: AsyncPage, req_id: str = "unknown") -> bool:
    """
    Force set UI state settings (isAdvancedOpen, areToolsOpen)

    Args:
        page: Playwright page instance
        req_id: request ID for logging

    Returns:
        bool: whether settings were applied successfully
    """
    try:
        logger.info(f"[{req_id}] Starting to force UI state settings...")

        # First verify current state
        current_state = await _verify_ui_state_settings(page, req_id)

        if not current_state['needsUpdate']:
            logger.info(f"[{req_id}] UI state already correct; no update needed")
            return True

        # Get existing preferences or create new
        prefs = current_state.get('prefs', {})

        # Force key settings
        prefs['isAdvancedOpen'] = True
        prefs['areToolsOpen'] = True

        # Save to localStorage
        prefs_str = json.dumps(prefs)
        await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", prefs_str)

        logger.info(f"[{req_id}] Forced: isAdvancedOpen=true, areToolsOpen=true")

        # Verify settings were applied
        verify_state = await _verify_ui_state_settings(page, req_id)
        if not verify_state['needsUpdate']:
            logger.info(f"[{req_id}] ✅ UI state verification successful")
            return True
        else:
            logger.warning(f"[{req_id}] ⚠️ UI state verification failed; may need retry")
            return False

    except Exception as e:
        logger.error(f"[{req_id}] Error while forcing UI state settings: {e}")
        return False

async def _force_ui_state_with_retry(page: AsyncPage, req_id: str = "unknown", max_retries: int = 3, retry_delay: float = 1.0) -> bool:
    """
    Force UI state settings with retry

    Args:
        page: Playwright page instance
        req_id: request ID for logging
        max_retries: maximum retries
        retry_delay: delay between retries (seconds)

    Returns:
        bool: whether settings were eventually applied
    """
    for attempt in range(1, max_retries + 1):
        logger.info(f"[{req_id}] Attempt to force UI state ({attempt}/{max_retries})")

        success = await _force_ui_state_settings(page, req_id)
        if success:
            logger.info(f"[{req_id}] ✅ UI state applied successfully on attempt {attempt}")
            return True

        if attempt < max_retries:
            logger.warning(f"[{req_id}] ⚠️ Attempt {attempt} failed; retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
        else:
            logger.error(f"[{req_id}] ❌ UI state failed after {max_retries} attempts")

    return False

async def _verify_and_apply_ui_state(page: AsyncPage, req_id: str = "unknown") -> bool:
    """
    Full flow to verify and apply UI state settings

    Args:
        page: Playwright page instance
        req_id: request ID for logging

    Returns:
        bool: whether operation succeeded
    """
    try:
        logger.info(f"[{req_id}] Starting verification and application of UI state settings...")

        # First verify current state
        state = await _verify_ui_state_settings(page, req_id)

        logger.info(f"[{req_id}] Current UI state: exists={state['exists']}, isAdvancedOpen={state['isAdvancedOpen']}, areToolsOpen={state['areToolsOpen']}, needsUpdate={state['needsUpdate']}")

        if state['needsUpdate']:
            logger.info(f"[{req_id}] Detected UI state needs update; applying forced settings...")
            return await _force_ui_state_with_retry(page, req_id)
        else:
            logger.info(f"[{req_id}] UI state already correct; no update needed")
            return True

    except Exception as e:
        logger.error(f"[{req_id}] Error verifying and applying UI state settings: {e}")
        return False

async def switch_ai_studio_model(page: AsyncPage, model_id: str, req_id: str) -> bool:
    """Switch AI Studio model"""
    logger.info(f"[{req_id}] Starting model switch to: {model_id}")
    original_prefs_str: Optional[str] = None
    original_prompt_model: Optional[str] = None
    new_chat_url = f"https://{AI_STUDIO_URL_PATTERN}prompts/new_chat"
    
    try:
        original_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        if original_prefs_str:
            try:
                original_prefs_obj = json.loads(original_prefs_str)
                original_prompt_model = original_prefs_obj.get("promptModel")
                logger.info(f"[{req_id}] Before switch localStorage.promptModel: {original_prompt_model or 'not set'}")
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] Unable to parse original aiStudioUserPreference JSON string.")
                original_prefs_str = None
        
        current_prefs_for_modification = json.loads(original_prefs_str) if original_prefs_str else {}
        full_model_path = f"models/{model_id}"
        
        if current_prefs_for_modification.get("promptModel") == full_model_path:
            logger.info(f"[{req_id}] Model already set to {model_id} (localStorage has target value); no switch needed")
            if page.url != new_chat_url:
                 logger.info(f"[{req_id}] Current URL is not new_chat ({page.url}); navigating to {new_chat_url}")
                 await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
                 await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
            return True
        
        logger.info(f"[{req_id}] Updating localStorage.promptModel from {current_prefs_for_modification.get('promptModel', 'unknown')} to {full_model_path}")
        current_prefs_for_modification["promptModel"] = full_model_path
        await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(current_prefs_for_modification))
        
        # Use new forced UI state function
        logger.info(f"[{req_id}] Applying forced UI state settings...")
        ui_state_success = await _verify_and_apply_ui_state(page, req_id)
        if not ui_state_success:
            logger.warning(f"[{req_id}] UI state settings failed; proceeding with model switch anyway")

        # For compatibility, also update current prefs object
        current_prefs_for_modification["isAdvancedOpen"] = True
        current_prefs_for_modification["areToolsOpen"] = True
        await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(current_prefs_for_modification))

        logger.info(f"[{req_id}] localStorage updated; navigating to '{new_chat_url}' to apply new model...")
        await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)

        input_field = page.locator(INPUT_SELECTOR)
        await expect_async(input_field).to_be_visible(timeout=30000)
        logger.info(f"[{req_id}] Navigated to new chat and page loaded; input field is visible")

        # After load, verify UI state settings again
        logger.info(f"[{req_id}] Page load complete; verifying UI state settings...")
        final_ui_state_success = await _verify_and_apply_ui_state(page, req_id)
        if final_ui_state_success:
            logger.info(f"[{req_id}] ✅ UI state final verification successful")
        else:
            logger.warning(f"[{req_id}] ⚠️ UI state final verification failed; continuing")
        
        final_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        final_prompt_model_in_storage: Optional[str] = None
        if final_prefs_str:
            try:
                final_prefs_obj = json.loads(final_prefs_str)
                final_prompt_model_in_storage = final_prefs_obj.get("promptModel")
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] Unable to parse refreshed aiStudioUserPreference JSON string.")
        
        if final_prompt_model_in_storage == full_model_path:
            logger.info(f"[{req_id}] ✅ AI Studio localStorage model successfully set to: {full_model_path}")
            
            page_display_match = False
            expected_display_name_for_target_id = None
            actual_displayed_model_name_on_page = "unreadable"
            
            # Get parsed_model_list
            import server
            parsed_model_list = getattr(server, 'parsed_model_list', [])
            
            if parsed_model_list:
                for m_obj in parsed_model_list:
                    if m_obj.get("id") == model_id:
                        expected_display_name_for_target_id = m_obj.get("display_name")
                        break

            try:
                model_name_locator = page.locator('[data-test-id="model-name"]')
                actual_displayed_model_id_on_page_raw = await model_name_locator.first.inner_text(timeout=5000)
                actual_displayed_model_id_on_page = actual_displayed_model_id_on_page_raw.strip()
                
                target_model_id = model_id

                if actual_displayed_model_id_on_page == target_model_id:
                    page_display_match = True
                    logger.info(f"[{req_id}] ✅ Page displayed model ID ('{actual_displayed_model_id_on_page}') matches expected ID ('{target_model_id}')")
                else:
                    page_display_match = False
                    logger.error(f"[{req_id}] ❌ Page displayed model ID ('{actual_displayed_model_id_on_page}') does not match expected ID ('{target_model_id}')")
            
            except Exception as e_disp:
                page_display_match = False # Treat read failure as mismatch
                logger.warning(f"[{req_id}] Error reading page displayed current model ID: {e_disp}. Cannot verify page display.")

            if page_display_match:
                try:
                    logger.info(f"[{req_id}] Model switch successful; re-enabling 'Temporary chat' mode...")
                    incognito_button_locator = page.locator('button[aria-label="Temporary chat toggle"]')
                    
                    await incognito_button_locator.wait_for(state="visible", timeout=5000)
                    
                    button_classes = await incognito_button_locator.get_attribute("class")
                    
                    if button_classes and 'ms-button-active' in button_classes:
                        logger.info(f"[{req_id}] 'Temporary chat' mode already active.")
                    else:
                        logger.info(f"[{req_id}] 'Temporary chat' mode inactive; clicking to enable...")
                        await incognito_button_locator.click(timeout=3000)
                        await asyncio.sleep(0.5)
                        
                        updated_classes = await incognito_button_locator.get_attribute("class")
                        if updated_classes and 'ms-button-active' in updated_classes:
                             logger.info(f"[{req_id}] ✅ 'Temporary chat' mode re-enabled successfully.")
                        else:
                             logger.warning(f"[{req_id}] ⚠️ After click, 'Temporary chat' mode state verification failed; may not have enabled.")
                
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ Failed to re-enable 'Temporary chat' mode after model switch: {e}")
                return True
            else:
                logger.error(f"[{req_id}] ❌ Model switch failed because page displayed model does not match expected (even if localStorage changed).")
        else:
            logger.error(f"[{req_id}] ❌ AI Studio did not accept model change (localStorage). Expected='{full_model_path}', actual='{final_prompt_model_in_storage or 'not set or invalid'}'.")
        
        logger.info(f"[{req_id}] Model switch failed. Attempting to restore to the model currently displayed on page...")
        current_displayed_name_for_revert_raw = "unreadable"
        current_displayed_name_for_revert_stripped = "unreadable"
        
        try:
            model_name_locator_revert = page.locator('[data-test-id="model-name"]')
            current_displayed_name_for_revert_raw = await model_name_locator_revert.first.inner_text(timeout=5000)
            current_displayed_name_for_revert_stripped = current_displayed_name_for_revert_raw.strip()
            logger.info(f"[{req_id}] Restore: page currently displays model name (raw: '{current_displayed_name_for_revert_raw}', stripped: '{current_displayed_name_for_revert_stripped}')")
        except Exception as e_read_disp_revert:
            logger.warning(f"[{req_id}] Restore: failed to read page displayed model name: {e_read_disp_revert}. Will try to revert to original localStorage.")
            if original_prefs_str:
                logger.info(f"[{req_id}] Restore: unable to read current page display; attempting to restore localStorage to original: '{original_prompt_model or 'not set'}'")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] Restore: navigating to '{new_chat_url}' to apply restored original localStorage...")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=20000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=20000)
                logger.info(f"[{req_id}] Restore: navigated to new chat and loaded; attempted to apply original localStorage.")
            else:
                logger.warning(f"[{req_id}] Restore: no valid original localStorage available and cannot read current page display.")
            return False
        
        model_id_to_revert_to = None
        if current_displayed_name_for_revert_stripped != "unreadable":
            model_id_to_revert_to = current_displayed_name_for_revert_stripped
            logger.info(f"[{req_id}] Restore: page currently displays ID '{model_id_to_revert_to}'; using it for revert.")
        else:
            if current_displayed_name_for_revert_stripped == "unreadable":
                 logger.warning(f"[{req_id}] Restore: cannot convert display name to ID due to unreadable page display.")
            else:
                 logger.warning(f"[{req_id}] Restore: parsed_model_list is empty; cannot convert display name '{current_displayed_name_for_revert_stripped}' to model ID.")
        
        if model_id_to_revert_to:
            base_prefs_for_final_revert = {}
            try:
                current_ls_content_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
                if current_ls_content_str:
                    base_prefs_for_final_revert = json.loads(current_ls_content_str)
                elif original_prefs_str:
                    base_prefs_for_final_revert = json.loads(original_prefs_str)
            except json.JSONDecodeError:
                logger.warning(f"[{req_id}] Restore: failed to parse existing localStorage to build revert preferences.")
            
            path_to_revert_to = f"models/{model_id_to_revert_to}"
            base_prefs_for_final_revert["promptModel"] = path_to_revert_to
            # Use new forced settings
            logger.info(f"[{req_id}] Restore: applying forced UI state settings...")
            ui_state_success = await _verify_and_apply_ui_state(page, req_id)
            if not ui_state_success:
                logger.warning(f"[{req_id}] Restore: UI state settings failed; continuing with revert")

            # For compatibility, also update current prefs object
            base_prefs_for_final_revert["isAdvancedOpen"] = True
            base_prefs_for_final_revert["areToolsOpen"] = True
            logger.info(f"[{req_id}] Restore: setting localStorage.promptModel to page displayed model path: '{path_to_revert_to}', and forcing settings")
            await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(base_prefs_for_final_revert))
            logger.info(f"[{req_id}] Restore: navigating to '{new_chat_url}' to apply revert to '{model_id_to_revert_to}'...")
            await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=30000)
            await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)

            # Verify UI state after revert
            logger.info(f"[{req_id}] Restore: page load complete; verifying UI state settings...")
            final_ui_state_success = await _verify_and_apply_ui_state(page, req_id)
            if final_ui_state_success:
                logger.info(f"[{req_id}] ✅ Restore: UI state final verification successful")
            else:
                logger.warning(f"[{req_id}] ⚠️ Restore: UI state final verification failed")

            logger.info(f"[{req_id}] Restore: navigated to new chat and loaded. localStorage should reflect model '{model_id_to_revert_to}'.")
        else:
            logger.error(f"[{req_id}] Restore: cannot revert model to page display state because a valid model ID could not be determined from display name '{current_displayed_name_for_revert_stripped}'.")
            if original_prefs_str:
                logger.warning(f"[{req_id}] Restore: as final fallback, attempting to restore original localStorage: '{original_prompt_model or 'not set'}'")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] Restore: navigating to '{new_chat_url}' to apply final fallback original localStorage.")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=20000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=20000)
                logger.info(f"[{req_id}] Restore: navigated to new chat and loaded; applied final fallback original localStorage.")
            else:
                logger.warning(f"[{req_id}] Restore: no valid original localStorage available as final fallback.")
        
        return False
        
    except Exception as e:
        logger.exception(f"[{req_id}] ❌ Critical error during model switch")
        # Import save_error_snapshot
        from .operations import save_error_snapshot
        await save_error_snapshot(f"model_switch_error_{req_id}")
        try:
            if original_prefs_str:
                logger.info(f"[{req_id}] Exception occurred; attempting to restore localStorage to: {original_prompt_model or 'not set'}")
                await page.evaluate("(origPrefs) => localStorage.setItem('aiStudioUserPreference', origPrefs)", original_prefs_str)
                logger.info(f"[{req_id}] Exception recovery: navigating to '{new_chat_url}' to apply restored localStorage.")
                await page.goto(new_chat_url, wait_until="domcontentloaded", timeout=15000)
                await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=15000)
        except Exception as recovery_err:
            logger.error(f"[{req_id}] Failed to restore localStorage after exception: {recovery_err}")
        return False

def load_excluded_models(filename: str):
    """Load excluded model list"""
    import server
    excluded_model_ids = getattr(server, 'excluded_model_ids', set())
    
    excluded_file_path = os.path.join(os.path.dirname(__file__), '..', filename)
    try:
        if os.path.exists(excluded_file_path):
            with open(excluded_file_path, 'r', encoding='utf-8') as f:
                loaded_ids = {line.strip() for line in f if line.strip()}
            if loaded_ids:
                excluded_model_ids.update(loaded_ids)
                server.excluded_model_ids = excluded_model_ids
                logger.info(f"✅ Loaded {len(loaded_ids)} models from '{filename}' into exclusion list: {excluded_model_ids}")
            else:
                logger.info(f"'{filename}' is empty or contains no valid model IDs; exclusion list unchanged.")
        else:
            logger.info(f"Exclusion list file '{filename}' not found; exclusion list is empty.")
    except Exception as e:
        logger.error(f"❌ Error loading excluded model list from '{filename}': {e}", exc_info=True)

async def _handle_initial_model_state_and_storage(page: AsyncPage):
    """Handle initial model state and storage"""
    import server
    current_ai_studio_model_id = getattr(server, 'current_ai_studio_model_id', None)
    parsed_model_list = getattr(server, 'parsed_model_list', [])
    model_list_fetch_event = getattr(server, 'model_list_fetch_event', None)
    
    logger.info("--- (New) Handle initial model state, localStorage, and isAdvancedOpen ---")
    needs_reload_and_storage_update = False
    reason_for_reload = ""
    
    try:
        initial_prefs_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
        if not initial_prefs_str:
            needs_reload_and_storage_update = True
            reason_for_reload = "localStorage.aiStudioUserPreference not found."
            logger.info(f"   Determined need to reload and update storage: {reason_for_reload}")
        else:
            logger.info("   Found 'aiStudioUserPreference' in localStorage. Parsing...")
            try:
                pref_obj = json.loads(initial_prefs_str)
                prompt_model_path = pref_obj.get("promptModel")
                is_advanced_open_in_storage = pref_obj.get("isAdvancedOpen")
                is_prompt_model_valid = isinstance(prompt_model_path, str) and prompt_model_path.strip()
                
                if not is_prompt_model_valid:
                    needs_reload_and_storage_update = True
                    reason_for_reload = "localStorage.promptModel invalid or not set."
                    logger.info(f"   Determined need to reload and update storage: {reason_for_reload}")
                else:
                    # Use new UI state verification functionality
                    ui_state = await _verify_ui_state_settings(page, "initial")
                    if ui_state['needsUpdate']:
                        needs_reload_and_storage_update = True
                        reason_for_reload = f"UI state needs update: isAdvancedOpen={ui_state['isAdvancedOpen']}, areToolsOpen={ui_state['areToolsOpen']} (expected: True)"
                        logger.info(f"   Determined need to reload and update storage: {reason_for_reload}")
                    else:
                        server.current_ai_studio_model_id = prompt_model_path.split('/')[-1]
                        logger.info(f"   ✅ localStorage valid and UI state correct. Initial model ID set from localStorage: {server.current_ai_studio_model_id}")
            except json.JSONDecodeError:
                needs_reload_and_storage_update = True
                reason_for_reload = "Failed to parse localStorage.aiStudioUserPreference JSON."
                logger.error(f"   Determined need to reload and update storage: {reason_for_reload}")
        
        if needs_reload_and_storage_update:
            logger.info(f"   Executing reload and storage update flow due to: {reason_for_reload}")
            logger.info("   Step 1: call _set_model_from_page_display(set_storage=True) to update localStorage and global model ID...")
            await _set_model_from_page_display(page, set_storage=True)
            
            current_page_url = page.url
            logger.info(f"   Step 2: reload page ({current_page_url}) to apply isAdvancedOpen=true...")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"   Attempt to reload page ({attempt + 1}/{max_retries}): {current_page_url}")
                    await page.goto(current_page_url, wait_until="domcontentloaded", timeout=40000)
                    await expect_async(page.locator(INPUT_SELECTOR)).to_be_visible(timeout=30000)
                    logger.info(f"   ✅ Page successfully reloaded to: {page.url}")

                    # Verify UI state after reload
                    logger.info(f"   Page reload complete; verifying UI state settings...")
                    reload_ui_state_success = await _verify_and_apply_ui_state(page, "reload")
                    if reload_ui_state_success:
                        logger.info(f"   ✅ UI state verification after reload successful")
                    else:
                        logger.warning(f"   ⚠️ UI state verification after reload failed")

                    break  # success
                except Exception as reload_err:
                    logger.warning(f"   ⚠️ Page reload attempt {attempt + 1}/{max_retries} failed: {reload_err}")
                    if attempt < max_retries - 1:
                        logger.info(f"   Will retry in 5s...")
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"   ❌ Page reload ultimately failed after {max_retries} attempts: {reload_err}. Subsequent model state may be inaccurate.", exc_info=True)
                        from .operations import save_error_snapshot
                        await save_error_snapshot(f"initial_storage_reload_fail_attempt_{attempt+1}")
            
            logger.info("   Step 3: after reload, call _set_model_from_page_display(set_storage=False) again to sync global model ID...")
            await _set_model_from_page_display(page, set_storage=False)
            logger.info(f"   ✅ Reload and storage update flow completed. Final global model ID: {server.current_ai_studio_model_id}")
        else:
            logger.info("   localStorage state is good (isAdvancedOpen=true, promptModel valid); no page reload needed.")
    except Exception as e:
        logger.error(f"❌ (New) Critical error handling initial model state and localStorage: {e}", exc_info=True)
        try:
            logger.warning("   Due to error, attempting fallback to set global model ID from page display only (no localStorage write)...")
            await _set_model_from_page_display(page, set_storage=False)
        except Exception as fallback_err:
            logger.error(f"   Fallback setting model ID also failed: {fallback_err}")

async def _set_model_from_page_display(page: AsyncPage, set_storage: bool = False):
    """Set model from page display"""
    import server
    current_ai_studio_model_id = getattr(server, 'current_ai_studio_model_id', None)
    parsed_model_list = getattr(server, 'parsed_model_list', [])
    model_list_fetch_event = getattr(server, 'model_list_fetch_event', None)
    
    try:
        logger.info("   Attempting to read current model name from page display element...")
        model_name_locator = page.locator('[data-test-id="model-name"]')
        displayed_model_name_from_page_raw = await model_name_locator.first.inner_text(timeout=7000)
        displayed_model_name = displayed_model_name_from_page_raw.strip()
        logger.info(f"   Page displayed model name (raw: '{displayed_model_name_from_page_raw}', stripped: '{displayed_model_name}')")
        
        found_model_id_from_display = None
        if model_list_fetch_event and not model_list_fetch_event.is_set():
            logger.info("   Waiting for model list data (up to 5s) to convert display name if needed...")
            try: 
                await asyncio.wait_for(model_list_fetch_event.wait(), timeout=5.0)
            except asyncio.TimeoutError: 
                logger.warning("   Waiting for model list timed out; may not accurately convert display name to ID.")
        
        found_model_id_from_display = displayed_model_name
        logger.info(f"   Page display shows a model ID directly: '{found_model_id_from_display}'")
        
        new_model_value = found_model_id_from_display
        if server.current_ai_studio_model_id != new_model_value:
            server.current_ai_studio_model_id = new_model_value
            logger.info(f"   Global current_ai_studio_model_id updated to: {server.current_ai_studio_model_id}")
        else:
            logger.info(f"   Global current_ai_studio_model_id ('{server.current_ai_studio_model_id}') matches page value; unchanged.")
        
        if set_storage:
            logger.info(f"   Preparing to set localStorage for page state (ensure isAdvancedOpen=true)...")
            existing_prefs_for_update_str = await page.evaluate("() => localStorage.getItem('aiStudioUserPreference')")
            prefs_to_set = {}
            if existing_prefs_for_update_str:
                try:
                    prefs_to_set = json.loads(existing_prefs_for_update_str)
                except json.JSONDecodeError:
                    logger.warning("   Failed to parse existing localStorage.aiStudioUserPreference; will create new preferences.")
            
            # Use new forced settings
            logger.info(f"     Applying forced UI state settings...")
            ui_state_success = await _verify_and_apply_ui_state(page, "set_model")
            if not ui_state_success:
                logger.warning(f"     UI state settings failed; using traditional method")
                prefs_to_set["isAdvancedOpen"] = True
                prefs_to_set["areToolsOpen"] = True
            else:
                # Ensure prefs_to_set also includes correct settings
                prefs_to_set["isAdvancedOpen"] = True
                prefs_to_set["areToolsOpen"] = True
            logger.info(f"     Forced isAdvancedOpen: true, areToolsOpen: true")
            
            if found_model_id_from_display:
                new_prompt_model_path = f"models/{found_model_id_from_display}"
                prefs_to_set["promptModel"] = new_prompt_model_path
                logger.info(f"     Set promptModel to: {new_prompt_model_path} (from page display)")
            elif "promptModel" not in prefs_to_set:
                logger.warning(f"     Could not determine model ID from page display '{displayed_model_name}', and localStorage has no existing promptModel. Will not set promptModel to avoid issues.")
            
            default_keys_if_missing = {
                "bidiModel": "models/gemini-1.0-pro-001",
                "isSafetySettingsOpen": False,
                "hasShownSearchGroundingTos": False,
                "autosaveEnabled": True,
                "theme": "system",
                "bidiOutputFormat": 3,
                "isSystemInstructionsOpen": False,
                "warmWelcomeDisplayed": True,
                "getCodeLanguage": "Node.js",
                "getCodeHistoryToggle": False,
                "fileCopyrightAcknowledged": True
            }
            for key, val_default in default_keys_if_missing.items():
                if key not in prefs_to_set:
                    prefs_to_set[key] = val_default
            
            await page.evaluate("(prefsStr) => localStorage.setItem('aiStudioUserPreference', prefsStr)", json.dumps(prefs_to_set))
            logger.info(f"   ✅ localStorage.aiStudioUserPreference updated. isAdvancedOpen: {prefs_to_set.get('isAdvancedOpen')}, areToolsOpen: {prefs_to_set.get('areToolsOpen')} (expected: True), promptModel: '{prefs_to_set.get('promptModel', 'not set/preserved')}'.")
    except Exception as e_set_disp:
        logger.error(f"   Error setting model from page display: {e_set_disp}", exc_info=True) 
