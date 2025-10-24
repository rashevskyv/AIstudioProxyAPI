"""
PageController module
Encapsulates complex logic for direct interactions with Playwright page.
"""
import asyncio
import re
from typing import Callable, List, Dict, Any, Optional

from playwright.async_api import Page as AsyncPage, expect as expect_async, TimeoutError

from config import (
    TEMPERATURE_INPUT_SELECTOR, MAX_OUTPUT_TOKENS_SELECTOR, STOP_SEQUENCE_INPUT_SELECTOR,
    MAT_CHIP_REMOVE_BUTTON_SELECTOR, TOP_P_INPUT_SELECTOR, SUBMIT_BUTTON_SELECTOR,
    CLEAR_CHAT_BUTTON_SELECTOR, CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR, OVERLAY_SELECTOR,
    PROMPT_TEXTAREA_SELECTOR, RESPONSE_CONTAINER_SELECTOR, RESPONSE_TEXT_SELECTOR,
    EDIT_MESSAGE_BUTTON_SELECTOR,USE_URL_CONTEXT_SELECTOR,UPLOAD_BUTTON_SELECTOR,
    SET_THINKING_BUDGET_TOGGLE_SELECTOR, THINKING_BUDGET_INPUT_SELECTOR,
    GROUNDING_WITH_GOOGLE_SEARCH_TOGGLE_SELECTOR
)
from config import (
    CLICK_TIMEOUT_MS, WAIT_FOR_ELEMENT_TIMEOUT_MS, CLEAR_CHAT_VERIFY_TIMEOUT_MS,
    DEFAULT_TEMPERATURE, DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_STOP_SEQUENCES, DEFAULT_TOP_P,
    ENABLE_URL_CONTEXT, ENABLE_THINKING_BUDGET, DEFAULT_THINKING_BUDGET, ENABLE_GOOGLE_SEARCH
)
from models import ClientDisconnectedError
from .operations import save_error_snapshot, _wait_for_response_completion, _get_final_response_content
from .initialization import enable_temporary_chat_mode

class PageController:
    """Encapsulates all interactions with the AI Studio page."""

    def __init__(self, page: AsyncPage, logger, req_id: str):
        self.page = page
        self.logger = logger
        self.req_id = req_id

    async def _check_disconnect(self, check_client_disconnected: Callable, stage: str):
        """Check whether client disconnected."""
        if check_client_disconnected(stage):
            raise ClientDisconnectedError(f"[{self.req_id}] Client disconnected at stage: {stage}")

    async def adjust_parameters(self, request_params: Dict[str, Any], page_params_cache: Dict[str, Any], params_cache_lock: asyncio.Lock, model_id_to_use: str, parsed_model_list: List[Dict[str, Any]], check_client_disconnected: Callable):
        """Adjust all request parameters."""
        self.logger.info(f"[{self.req_id}] Starting parameter adjustments...")
        await self._check_disconnect(check_client_disconnected, "Start Parameter Adjustment")

        # Temperature
        temp_to_set = request_params.get('temperature', DEFAULT_TEMPERATURE)
        await self._adjust_temperature(temp_to_set, page_params_cache, params_cache_lock, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "After Temperature Adjustment")

        # Max tokens
        max_tokens_to_set = request_params.get('max_output_tokens', DEFAULT_MAX_OUTPUT_TOKENS)
        await self._adjust_max_tokens(max_tokens_to_set, page_params_cache, params_cache_lock, model_id_to_use, parsed_model_list, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "After Max Tokens Adjustment")

        # Stop sequences
        stop_to_set = request_params.get('stop', DEFAULT_STOP_SEQUENCES)
        await self._adjust_stop_sequences(stop_to_set, page_params_cache, params_cache_lock, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "After Stop Sequences Adjustment")

        # Top P
        top_p_to_set = request_params.get('top_p', DEFAULT_TOP_P)
        await self._adjust_top_p(top_p_to_set, check_client_disconnected)
        await self._check_disconnect(check_client_disconnected, "End Parameter Adjustment")

        # Ensure tools panel expanded for advanced settings
        await self._ensure_tools_panel_expanded(check_client_disconnected)

        # URL CONTEXT
        if ENABLE_URL_CONTEXT:
            await self._open_url_content(check_client_disconnected)
        else:
            self.logger.info(f"[{self.req_id}] URL Context disabled; skipping.")

        # Thinking budget
        await self._handle_thinking_budget(request_params, check_client_disconnected)

        # Google Search
        await self._adjust_google_search(request_params, check_client_disconnected)

    async def _handle_thinking_budget(self, request_params: Dict[str, Any], check_client_disconnected: Callable):
        """Adjust thinking budget according to reasoning_effort."""
        reasoning_effort = request_params.get('reasoning_effort')

        # Check whether user explicitly disabled thinking budget
        should_disable_budget = isinstance(reasoning_effort, str) and reasoning_effort.lower() == 'none'

        if should_disable_budget:
            self.logger.info(f"[{self.req_id}] User disabled thinking budget via reasoning_effort='none'.")
            await self._control_thinking_budget_toggle(should_be_checked=False, check_client_disconnected=check_client_disconnected)
        elif reasoning_effort is not None:
            # User specified a non-'none' value; enable and set
            self.logger.info(f"[{self.req_id}] User specified reasoning_effort: {reasoning_effort}; enabling and setting thinking budget.")
            await self._control_thinking_budget_toggle(should_be_checked=True, check_client_disconnected=check_client_disconnected)
            await self._adjust_thinking_budget(reasoning_effort, check_client_disconnected)
        else:
            # User didn't specify; use default config
            self.logger.info(f"[{self.req_id}] User didn't specify reasoning_effort; using default ENABLE_THINKING_BUDGET: {ENABLE_THINKING_BUDGET}.")
            await self._control_thinking_budget_toggle(should_be_checked=ENABLE_THINKING_BUDGET, check_client_disconnected=check_client_disconnected)
            if ENABLE_THINKING_BUDGET:
                # If default enabled, use default value
                await self._adjust_thinking_budget(None, check_client_disconnected)

    def _parse_thinking_budget(self, reasoning_effort: Optional[Any]) -> Optional[int]:
        """Parse token_budget from reasoning_effort."""
        token_budget = None
        if reasoning_effort is None:
            token_budget = DEFAULT_THINKING_BUDGET
            self.logger.info(f"[{self.req_id}] 'reasoning_effort' is None; using default thinking budget: {token_budget}")
        elif isinstance(reasoning_effort, int):
            token_budget = reasoning_effort
        elif isinstance(reasoning_effort, str):
            if reasoning_effort.lower() == 'none':
                token_budget = DEFAULT_THINKING_BUDGET
                self.logger.info(f"[{self.req_id}] 'reasoning_effort' is 'none' string; using default thinking budget: {token_budget}")
            else:
                effort_map = {
                    "low": 1000,
                    "medium": 8000,
                    "high": 24000
                }
                token_budget = effort_map.get(reasoning_effort.lower())
                if token_budget is None:
                    try:
                        token_budget = int(reasoning_effort)
                    except (ValueError, TypeError):
                        pass # token_budget remains None
        
        if token_budget is None:
             self.logger.warning(f"[{self.req_id}] Could not parse a valid token_budget from '{reasoning_effort}' (type: {type(reasoning_effort)}).")

        return token_budget

    async def _adjust_thinking_budget(self, reasoning_effort: Optional[Any], check_client_disconnected: Callable):
        """Adjust thinking budget according to reasoning_effort."""
        self.logger.info(f"[{self.req_id}] Checking and adjusting thinking budget, input: {reasoning_effort}")
        
        token_budget = self._parse_thinking_budget(reasoning_effort)

        if token_budget is None:
            self.logger.warning(f"[{self.req_id}] Invalid reasoning_effort value: '{reasoning_effort}'. Skipping.")
            return

        budget_input_locator = self.page.locator(THINKING_BUDGET_INPUT_SELECTOR)
        
        try:
            await expect_async(budget_input_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Thinking budget adjustment - after input visible")
            
            self.logger.info(f"[{self.req_id}] Setting thinking budget to: {token_budget}")
            await budget_input_locator.fill(str(token_budget), timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Thinking budget adjustment - after input filled")

            # Verify
            await asyncio.sleep(0.1)
            new_value_str = await budget_input_locator.input_value(timeout=3000)
            if int(new_value_str) == token_budget:
                self.logger.info(f"[{self.req_id}] ✅ Thinking budget successfully updated to: {new_value_str}")
            else:
                self.logger.warning(f"[{self.req_id}] ⚠️ Thinking budget verification failed. Page shows: {new_value_str}, expected: {token_budget}")

        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error adjusting thinking budget: {e}")
            if isinstance(e, ClientDisconnectedError):
                raise

    def _should_enable_google_search(self, request_params: Dict[str, Any]) -> bool:
        """Determine whether Google Search should be enabled based on request params or defaults."""
        if 'tools' in request_params and request_params.get('tools') is not None:
            tools = request_params.get('tools')
            has_google_search_tool = False
            if isinstance(tools, list):
                for tool in tools:
                    if isinstance(tool, dict):
                        if tool.get('google_search_retrieval') is not None:
                            has_google_search_tool = True
                            break
                        if tool.get('function', {}).get('name') == 'googleSearch':
                            has_google_search_tool = True
                            break
            self.logger.info(f"[{self.req_id}] Request contains 'tools' param. Detected Google Search tool: {has_google_search_tool}.")
            return has_google_search_tool
        else:
            self.logger.info(f"[{self.req_id}] Request has no 'tools' param. Using default ENABLE_GOOGLE_SEARCH: {ENABLE_GOOGLE_SEARCH}.")
            return ENABLE_GOOGLE_SEARCH

    async def _adjust_google_search(self, request_params: Dict[str, Any], check_client_disconnected: Callable):
        """Control Google Search toggle based on request params or defaults."""
        self.logger.info(f"[{self.req_id}] Checking and adjusting Google Search toggle...")

        should_enable_search = self._should_enable_google_search(request_params)

        toggle_selector = GROUNDING_WITH_GOOGLE_SEARCH_TOGGLE_SELECTOR
        
        try:
            toggle_locator = self.page.locator(toggle_selector)
            await expect_async(toggle_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Google Search toggle - after element visible")

            is_checked_str = await toggle_locator.get_attribute("aria-checked")
            is_currently_checked = is_checked_str == "true"
            self.logger.info(f"[{self.req_id}] Google Search toggle current state: '{is_checked_str}'. Expected: {should_enable_search}")

            if should_enable_search != is_currently_checked:
                action = "enable" if should_enable_search else "disable"
                self.logger.info(f"[{self.req_id}] Google Search toggle not in expected state; clicking to {action}...")
                await toggle_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, f"Google Search toggle - after click to {action}")
                await asyncio.sleep(0.5) # Wait for UI to update
                new_state = await toggle_locator.get_attribute("aria-checked")
                if (new_state == "true") == should_enable_search:
                    self.logger.info(f"[{self.req_id}] ✅ Google Search toggle {action}d successfully.")
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ Google Search toggle {action} failed. Current state: '{new_state}'")
            else:
                self.logger.info(f"[{self.req_id}] Google Search toggle already in expected state; no action needed.")

        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error operating 'Google Search toggle': {e}")
            if isinstance(e, ClientDisconnectedError):
                 raise

    async def _ensure_tools_panel_expanded(self, check_client_disconnected: Callable):
        """Ensure the panel with advanced tools (URL context, thinking budget, etc.) is expanded."""
        self.logger.info(f"[{self.req_id}] Checking and ensuring tools panel is expanded...")
        try:
            collapse_tools_locator = self.page.locator('button[aria-label="Expand or collapse tools"]')
            await expect_async(collapse_tools_locator).to_be_visible(timeout=5000)
            
            grandparent_locator = collapse_tools_locator.locator("xpath=../..")
            class_string = await grandparent_locator.get_attribute("class", timeout=3000)

            if class_string and "expanded" not in class_string.split():
                self.logger.info(f"[{self.req_id}] Tools panel collapsed; clicking to expand...")
                await collapse_tools_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, "After tools panel expand")
                # Wait for expand animation
                await expect_async(grandparent_locator).to_have_class(re.compile(r'.*expanded.*'), timeout=5000)
                self.logger.info(f"[{self.req_id}] ✅ Tools panel expanded.")
            else:
                self.logger.info(f"[{self.req_id}] Tools panel already expanded.")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error expanding tools panel: {e}")
            # Continue with subsequent operations but record error
            if isinstance(e, ClientDisconnectedError):
                raise

    async def _open_url_content(self,check_client_disconnected: Callable):
        """Only toggles URL Context switch; assumes panel is expanded."""
        try:
            self.logger.info(f"[{self.req_id}] Checking and enabling URL Context toggle...")
            use_url_content_selector = self.page.locator(USE_URL_CONTEXT_SELECTOR)
            await expect_async(use_url_content_selector).to_be_visible(timeout=5000)
            
            is_checked = await use_url_content_selector.get_attribute("aria-checked")
            if "false" == is_checked:
                self.logger.info(f"[{self.req_id}] URL Context toggle off; clicking to turn on...")
                await use_url_content_selector.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, "After URLCONTEXT click")
                self.logger.info(f"[{self.req_id}] ✅ URL Context toggle clicked.")
            else:
                self.logger.info(f"[{self.req_id}] URL Context toggle already on.")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error operating USE_URL_CONTEXT_SELECTOR: {e}.")
            if isinstance(e, ClientDisconnectedError):
                raise

    async def _control_thinking_budget_toggle(self, should_be_checked: bool, check_client_disconnected: Callable):
        """
        Control "Thinking Budget" toggle state based on should_be_checked.
        """
        toggle_selector = SET_THINKING_BUDGET_TOGGLE_SELECTOR
        self.logger.info(f"[{self.req_id}] Control 'Thinking Budget' toggle; expected state: {'checked' if should_be_checked else 'unchecked'}...")

        try:
            toggle_locator = self.page.locator(toggle_selector)
            await expect_async(toggle_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Thinking budget toggle - after element visible")

            is_checked_str = await toggle_locator.get_attribute("aria-checked")
            current_state_is_checked = is_checked_str == "true"
            self.logger.info(f"[{self.req_id}] Thinking budget toggle current 'aria-checked': {is_checked_str} (checked: {current_state_is_checked})")

            if current_state_is_checked != should_be_checked:
                action = "enable" if should_be_checked else "disable"
                self.logger.info(f"[{self.req_id}] Thinking budget toggle not in expected state; clicking to {action}...")
                await toggle_locator.click(timeout=CLICK_TIMEOUT_MS)
                await self._check_disconnect(check_client_disconnected, f"Thinking budget toggle - after click to {action}")

                await asyncio.sleep(0.5)
                new_state_str = await toggle_locator.get_attribute("aria-checked")
                new_state_is_checked = new_state_str == "true"

                if new_state_is_checked == should_be_checked:
                    self.logger.info(f"[{self.req_id}] ✅ 'Thinking Budget' toggle {action}d successfully. New state: {new_state_str}")
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ 'Thinking Budget' toggle verification failed after {action}. Expected: '{should_be_checked}', actual: '{new_state_str}'")
            else:
                self.logger.info(f"[{self.req_id}] 'Thinking Budget' toggle already in expected state; no action needed.")

        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error operating 'Thinking Budget toggle': {e}")
            if isinstance(e, ClientDisconnectedError):
                raise
    async def _adjust_temperature(self, temperature: float, page_params_cache: dict, params_cache_lock: asyncio.Lock, check_client_disconnected: Callable):
        """Adjust temperature parameter."""
        async with params_cache_lock:
            self.logger.info(f"[{self.req_id}] Checking and adjusting temperature...")
            clamped_temp = max(0.0, min(2.0, temperature))
            if clamped_temp != temperature:
                self.logger.warning(f"[{self.req_id}] Requested temperature {temperature} out of range [0, 2]; clamped to {clamped_temp}")

            cached_temp = page_params_cache.get("temperature")
            if cached_temp is not None and abs(cached_temp - clamped_temp) < 0.001:
                self.logger.info(f"[{self.req_id}] Temperature ({clamped_temp}) matches cached value ({cached_temp}). Skipping page interaction.")
                return

            self.logger.info(f"[{self.req_id}] Requested temperature ({clamped_temp}) differs from cache ({cached_temp}); updating UI.")
            temp_input_locator = self.page.locator(TEMPERATURE_INPUT_SELECTOR)


            try:
                await expect_async(temp_input_locator).to_be_visible(timeout=5000)
                await self._check_disconnect(check_client_disconnected, "Temperature adjustment - after input visible")

                current_temp_str = await temp_input_locator.input_value(timeout=3000)
                await self._check_disconnect(check_client_disconnected, "Temperature adjustment - after input read")

                current_temp_float = float(current_temp_str)
                self.logger.info(f"[{self.req_id}] Page current temperature: {current_temp_float}, requested: {clamped_temp}")

                if abs(current_temp_float - clamped_temp) < 0.001:
                    self.logger.info(f"[{self.req_id}] Page temperature ({current_temp_float}) equals requested ({clamped_temp}). Updating cache and skipping write.")
                    page_params_cache["temperature"] = current_temp_float
                else:
                    self.logger.info(f"[{self.req_id}] Page temperature ({current_temp_float}) differs from requested ({clamped_temp}); updating...")
                    await temp_input_locator.fill(str(clamped_temp), timeout=5000)
                    await self._check_disconnect(check_client_disconnected, "Temperature adjustment - after input filled")

                    await asyncio.sleep(0.1)
                    new_temp_str = await temp_input_locator.input_value(timeout=3000)
                    new_temp_float = float(new_temp_str)

                    if abs(new_temp_float - clamped_temp) < 0.001:
                        self.logger.info(f"[{self.req_id}] ✅ Temperature updated to: {new_temp_float}. Cache updated.")
                        page_params_cache["temperature"] = new_temp_float
                    else:
                        self.logger.warning(f"[{self.req_id}] ⚠️ Temperature verification failed. Page shows: {new_temp_float}, expected: {clamped_temp}. Clearing cache.")
                        page_params_cache.pop("temperature", None)
                        await save_error_snapshot(f"temperature_verify_fail_{self.req_id}")

            except ValueError as ve:
                self.logger.error(f"[{self.req_id}] Error converting temperature value to float. Err: {ve}. Clearing cache.")
                page_params_cache.pop("temperature", None)
                await save_error_snapshot(f"temperature_value_error_{self.req_id}")
            except Exception as pw_err:
                self.logger.error(f"[{self.req_id}] ❌ Error operating temperature input: {pw_err}. Clearing cache.")
                page_params_cache.pop("temperature", None)
                await save_error_snapshot(f"temperature_playwright_error_{self.req_id}")
                if isinstance(pw_err, ClientDisconnectedError):
                    raise

    async def _adjust_max_tokens(self, max_tokens: int, page_params_cache: dict, params_cache_lock: asyncio.Lock, model_id_to_use: str, parsed_model_list: list, check_client_disconnected: Callable):
        """Adjust max output tokens parameter."""
        async with params_cache_lock:
            self.logger.info(f"[{self.req_id}] Checking and adjusting max output tokens...")
            min_val_for_tokens = 1
            max_val_for_tokens_from_model = 65536

            if model_id_to_use and parsed_model_list:
                current_model_data = next((m for m in parsed_model_list if m.get("id") == model_id_to_use), None)
                if current_model_data and current_model_data.get("supported_max_output_tokens") is not None:
                    try:
                        supported_tokens = int(current_model_data["supported_max_output_tokens"])
                        if supported_tokens > 0:
                            max_val_for_tokens_from_model = supported_tokens
                        else:
                            self.logger.warning(f"[{self.req_id}] Model {model_id_to_use} supported_max_output_tokens invalid: {supported_tokens}")
                    except (ValueError, TypeError):
                        self.logger.warning(f"[{self.req_id}] Model {model_id_to_use} supported_max_output_tokens parse failed")

            clamped_max_tokens = max(min_val_for_tokens, min(max_val_for_tokens_from_model, max_tokens))
            if clamped_max_tokens != max_tokens:
                self.logger.warning(f"[{self.req_id}] Requested max output tokens {max_tokens} out of model range; clamped to {clamped_max_tokens}")

            cached_max_tokens = page_params_cache.get("max_output_tokens")
            if cached_max_tokens is not None and cached_max_tokens == clamped_max_tokens:
                self.logger.info(f"[{self.req_id}] Max output tokens ({clamped_max_tokens}) matches cache. Skipping page interaction.")
                return

            max_tokens_input_locator = self.page.locator(MAX_OUTPUT_TOKENS_SELECTOR)

            try:
                await expect_async(max_tokens_input_locator).to_be_visible(timeout=5000)
                await self._check_disconnect(check_client_disconnected, "Max tokens adjustment - after input visible")

                current_max_tokens_str = await max_tokens_input_locator.input_value(timeout=3000)
                current_max_tokens_int = int(current_max_tokens_str)

                if current_max_tokens_int == clamped_max_tokens:
                    self.logger.info(f"[{self.req_id}] Page max output tokens ({current_max_tokens_int}) equals requested ({clamped_max_tokens}). Updating cache and skipping write.")
                    page_params_cache["max_output_tokens"] = current_max_tokens_int
                else:
                    self.logger.info(f"[{self.req_id}] Page max output tokens ({current_max_tokens_int}) differs from requested ({clamped_max_tokens}); updating...")
                    await max_tokens_input_locator.fill(str(clamped_max_tokens), timeout=5000)
                    await self._check_disconnect(check_client_disconnected, "Max tokens adjustment - after input filled")

                    await asyncio.sleep(0.1)
                    new_max_tokens_str = await max_tokens_input_locator.input_value(timeout=3000)
                    new_max_tokens_int = int(new_max_tokens_str)

                    if new_max_tokens_int == clamped_max_tokens:
                        self.logger.info(f"[{self.req_id}] ✅ Max output tokens updated to: {new_max_tokens_int}")
                        page_params_cache["max_output_tokens"] = new_max_tokens_int
                    else:
                        self.logger.warning(f"[{self.req_id}] ⚠️ Max output tokens verification failed. Page shows: {new_max_tokens_int}, expected: {clamped_max_tokens}. Clearing cache.")
                        page_params_cache.pop("max_output_tokens", None)
                        await save_error_snapshot(f"max_tokens_verify_fail_{self.req_id}")

            except (ValueError, TypeError) as ve:
                self.logger.error(f"[{self.req_id}] Error converting max output tokens: {ve}. Clearing cache.")
                page_params_cache.pop("max_output_tokens", None)
                await save_error_snapshot(f"max_tokens_value_error_{self.req_id}")
            except Exception as e:
                self.logger.error(f"[{self.req_id}] ❌ Error adjusting max output tokens: {e}. Clearing cache.")
                page_params_cache.pop("max_output_tokens", None)
                await save_error_snapshot(f"max_tokens_error_{self.req_id}")
                if isinstance(e, ClientDisconnectedError):
                    raise
    
    async def _adjust_stop_sequences(self, stop_sequences, page_params_cache: dict, params_cache_lock: asyncio.Lock, check_client_disconnected: Callable):
        """Adjust stop sequences."""
        async with params_cache_lock:
            self.logger.info(f"[{self.req_id}] Checking and setting stop sequences...")

            # Normalize stop_sequences input types
            normalized_requested_stops = set()
            if stop_sequences is not None:
                if isinstance(stop_sequences, str):
                    # Single string
                    if stop_sequences.strip():
                        normalized_requested_stops.add(stop_sequences.strip())
                elif isinstance(stop_sequences, list):
                    # List of strings
                    for s in stop_sequences:
                        if isinstance(s, str) and s.strip():
                            normalized_requested_stops.add(s.strip())

            cached_stops_set = page_params_cache.get("stop_sequences")

            if cached_stops_set is not None and cached_stops_set == normalized_requested_stops:
                self.logger.info(f"[{self.req_id}] Requested stop sequences match cache; skipping page interaction.")
                return

            stop_input_locator = self.page.locator(STOP_SEQUENCE_INPUT_SELECTOR)
            remove_chip_buttons_locator = self.page.locator(MAT_CHIP_REMOVE_BUTTON_SELECTOR)

            try:
                # Clear existing stop sequences
                initial_chip_count = await remove_chip_buttons_locator.count()
                removed_count = 0
                max_removals = initial_chip_count + 5

                while await remove_chip_buttons_locator.count() > 0 and removed_count < max_removals:
                    await self._check_disconnect(check_client_disconnected, "Stop sequence clearing - loop start")
                    try:
                        await remove_chip_buttons_locator.first.click(timeout=2000)
                        removed_count += 1
                        await asyncio.sleep(0.15)
                    except Exception:
                        break

                # Add new stop sequences
                if normalized_requested_stops:
                    await expect_async(stop_input_locator).to_be_visible(timeout=5000)
                    for seq in normalized_requested_stops:
                        await stop_input_locator.fill(seq, timeout=3000)
                        await stop_input_locator.press("Enter", timeout=3000)
                        await asyncio.sleep(0.2)

                page_params_cache["stop_sequences"] = normalized_requested_stops
                self.logger.info(f"[{self.req_id}] ✅ Stop sequences set. Cache updated.")

            except Exception as e:
                self.logger.error(f"[{self.req_id}] ❌ Error setting stop sequences: {e}")
                page_params_cache.pop("stop_sequences", None)
                await save_error_snapshot(f"stop_sequence_error_{self.req_id}")
                if isinstance(e, ClientDisconnectedError):
                    raise

    async def _adjust_top_p(self, top_p: float, check_client_disconnected: Callable):
        """Adjust Top P parameter."""
        self.logger.info(f"[{self.req_id}] Checking and adjusting Top P...")
        clamped_top_p = max(0.0, min(1.0, top_p))

        if abs(clamped_top_p - top_p) > 1e-9:
            self.logger.warning(f"[{self.req_id}] Requested Top P {top_p} out of range [0, 1]; clamped to {clamped_top_p}")

        top_p_input_locator = self.page.locator(TOP_P_INPUT_SELECTOR)
        try:
            await expect_async(top_p_input_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "Top P adjustment - after input visible")

            current_top_p_str = await top_p_input_locator.input_value(timeout=3000)
            current_top_p_float = float(current_top_p_str)

            if abs(current_top_p_float - clamped_top_p) > 1e-9:
                self.logger.info(f"[{self.req_id}] Page Top P ({current_top_p_float}) differs from requested ({clamped_top_p}); updating...")
                await top_p_input_locator.fill(str(clamped_top_p), timeout=5000)
                await self._check_disconnect(check_client_disconnected, "Top P adjustment - after input filled")

                # Verify
                await asyncio.sleep(0.1)
                new_top_p_str = await top_p_input_locator.input_value(timeout=3000)
                new_top_p_float = float(new_top_p_str)

                if abs(new_top_p_float - clamped_top_p) <= 1e-9:
                    self.logger.info(f"[{self.req_id}] ✅ Top P updated to: {new_top_p_float}")
                else:
                    self.logger.warning(f"[{self.req_id}] ⚠️ Top P verification failed. Page shows: {new_top_p_float}, expected: {clamped_top_p}")
                    await save_error_snapshot(f"top_p_verify_fail_{self.req_id}")
            else:
                self.logger.info(f"[{self.req_id}] Page Top P ({current_top_p_float}) equals requested ({clamped_top_p}); no change")

        except (ValueError, TypeError) as ve:
            self.logger.error(f"[{self.req_id}] Error converting Top P value: {ve}")
            await save_error_snapshot(f"top_p_value_error_{self.req_id}")
        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error adjusting Top P: {e}")
            await save_error_snapshot(f"top_p_error_{self.req_id}")
            if isinstance(e, ClientDisconnectedError):
                raise

    async def clear_chat_history(self, check_client_disconnected: Callable):
        """Clear chat history."""
        self.logger.info(f"[{self.req_id}] Starting to clear chat history...")
        await self._check_disconnect(check_client_disconnected, "Start Clear Chat")

        try:
            # Typically encountered in streaming proxy mode where streaming output ended but AI continues generating;
            # clear button gets locked while page still at /new_chat; skipping clear would block subsequent requests.
            # Hence, check and click submit button (acts as Stop) first.
            submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
            try:
                self.logger.info(f"[{self.req_id}] Checking submit button state...")
                # Use short timeout (1s) to avoid blocking; not core to clear flow
                await expect_async(submit_button_locator).to_be_enabled(timeout=1000)
                self.logger.info(f"[{self.req_id}] Submit button enabled; clicking and waiting 1s...")
                await submit_button_locator.click(timeout=CLICK_TIMEOUT_MS)
                await asyncio.sleep(1.0)
                self.logger.info(f"[{self.req_id}] Submit button click done.")
            except Exception as e_submit:
                # If submit not enabled or Playwright errors; continue
                self.logger.info(f"[{self.req_id}] Submit button not enabled or check/click errored. Proceeding to clear.")

            clear_chat_button_locator = self.page.locator(CLEAR_CHAT_BUTTON_SELECTOR)
            confirm_button_locator = self.page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)
            overlay_locator = self.page.locator(OVERLAY_SELECTOR)

            can_attempt_clear = False
            try:
                await expect_async(clear_chat_button_locator).to_be_enabled(timeout=3000)
                can_attempt_clear = True
                self.logger.info(f"[{self.req_id}] \"Clear chat\" button enabled; continuing clear flow.")
            except Exception as e_enable:
                is_new_chat_url = '/prompts/new_chat' in self.page.url.rstrip('/')
                if is_new_chat_url:
                    self.logger.info(f"[{self.req_id}] \"Clear chat\" button disabled (expected on new_chat). Skipping clear.")
                else:
                    self.logger.warning(f"[{self.req_id}] Failed waiting for \"Clear chat\" button enabled: {e_enable}. Clear may not proceed.")

            await self._check_disconnect(check_client_disconnected, "Clear chat - after enable check")

            if can_attempt_clear:
                await self._execute_chat_clear(clear_chat_button_locator, confirm_button_locator, overlay_locator, check_client_disconnected)
                await self._verify_chat_cleared(check_client_disconnected)
                self.logger.info(f"[{self.req_id}] Chat cleared; re-enabling 'Temporary chat' mode...")
                await enable_temporary_chat_mode(self.page)

        except Exception as e_clear:
            self.logger.error(f"[{self.req_id}] Error during chat clear: {e_clear}")
            if not (isinstance(e_clear, ClientDisconnectedError) or (hasattr(e_clear, 'name') and 'Disconnect' in e_clear.name)):
                await save_error_snapshot(f"clear_chat_error_{self.req_id}")
            raise

    async def _execute_chat_clear(self, clear_chat_button_locator, confirm_button_locator, overlay_locator, check_client_disconnected: Callable):
        """Execute clear chat operation"""
        overlay_initially_visible = False
        try:
            if await overlay_locator.is_visible(timeout=1000):
                overlay_initially_visible = True
                self.logger.info(f"[{self.req_id}] Clear chat confirm overlay visible initially. Clicking \"Continue\" directly.")
        except TimeoutError:
            self.logger.info(f"[{self.req_id}] Clear chat confirm overlay initially not visible (timeout or not found).")
            overlay_initially_visible = False
        except Exception as e_vis_check:
            self.logger.warning(f"[{self.req_id}] Error checking overlay visibility: {e_vis_check}. Assuming not visible.")
            overlay_initially_visible = False

        await self._check_disconnect(check_client_disconnected, "Clear chat - after initial overlay check")

        if overlay_initially_visible:
            self.logger.info(f"[{self.req_id}] Clicking \"Continue\" (overlay exists): {CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR}")
            await confirm_button_locator.click(timeout=CLICK_TIMEOUT_MS)
        else:
            self.logger.info(f"[{self.req_id}] Clicking \"Clear chat\" button: {CLEAR_CHAT_BUTTON_SELECTOR}")
            # If transparent backdrops intercept clicks, try to dismiss first
            try:
                await self._dismiss_backdrops()
            except Exception:
                pass
            try:
                await clear_chat_button_locator.click(timeout=CLICK_TIMEOUT_MS)
            except Exception as first_click_err:
                # Attempt cleanup and force click as fallback
                self.logger.warning(f"[{self.req_id}] First clear button click failed; cleaning backdrops and force-clicking: {first_click_err}")
                try:
                    await self._dismiss_backdrops()
                except Exception:
                    pass
                try:
                    await clear_chat_button_locator.click(timeout=CLICK_TIMEOUT_MS, force=True)
                except Exception as force_click_err:
                    self.logger.error(f"[{self.req_id}] Force click on clear button still failed: {force_click_err}")
                    raise
            await self._check_disconnect(check_client_disconnected, "Clear chat - after clear click")

            try:
                self.logger.info(f"[{self.req_id}] Waiting for clear chat confirm overlay to appear: {OVERLAY_SELECTOR}")
                await expect_async(overlay_locator).to_be_visible(timeout=WAIT_FOR_ELEMENT_TIMEOUT_MS)
                self.logger.info(f"[{self.req_id}] Clear chat confirm overlay appeared.")
            except TimeoutError:
                error_msg = f"Timed out waiting for clear chat confirm overlay (after clear click). req_id: {self.req_id}"
                self.logger.error(error_msg)
                await save_error_snapshot(f"clear_chat_overlay_timeout_{self.req_id}")
                raise Exception(error_msg)

            await self._check_disconnect(check_client_disconnected, "Clear chat - after overlay appeared")
            self.logger.info(f"[{self.req_id}] Clicking \"Continue\" (in dialog): {CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR}")
            await confirm_button_locator.click(timeout=CLICK_TIMEOUT_MS)

        await self._check_disconnect(check_client_disconnected, "Clear chat - after Continue click")

        # Wait for dialog disappearance
        max_retries_disappear = 3
        for attempt_disappear in range(max_retries_disappear):
            try:
                self.logger.info(f"[{self.req_id}] Waiting for clear chat confirm button/dialog to disappear (attempt {attempt_disappear + 1}/{max_retries_disappear})...")
                await expect_async(confirm_button_locator).to_be_hidden(timeout=CLEAR_CHAT_VERIFY_TIMEOUT_MS)
                await expect_async(overlay_locator).to_be_hidden(timeout=1000)
                self.logger.info(f"[{self.req_id}] ✅ Clear chat confirm dialog disappeared.")
                break
            except TimeoutError:
                self.logger.warning(f"[{self.req_id}] ⚠️ Timeout waiting for clear chat confirm dialog to disappear (attempt {attempt_disappear + 1}/{max_retries_disappear}).")
                if attempt_disappear < max_retries_disappear - 1:
                    await asyncio.sleep(1.0)
                    await self._check_disconnect(check_client_disconnected, f"Clear chat - before retry disappear check {attempt_disappear + 1}")
                    continue
                else:
                    error_msg = f"Max retries reached. Clear chat confirm dialog did not disappear. req_id: {self.req_id}"
                    self.logger.error(error_msg)
                    await save_error_snapshot(f"clear_chat_dialog_disappear_timeout_{self.req_id}")
                    raise Exception(error_msg)
            except ClientDisconnectedError:
                self.logger.info(f"[{self.req_id}] Client disconnected while waiting for clear confirm dialog to disappear.")
                raise
            except Exception as other_err:
                self.logger.warning(f"[{self.req_id}] Other error while waiting for clear confirm dialog to disappear: {other_err}")
                if attempt_disappear < max_retries_disappear - 1:
                    await asyncio.sleep(1.0)
                    continue
                else:
                    raise

            await self._check_disconnect(check_client_disconnected, f"Clear chat - after disappear check attempt {attempt_disappear + 1}")

    async def _dismiss_backdrops(self):
        """Try closing lingering cdk transparent backdrops to avoid click interception."""
        try:
            backdrop = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-backdrop-showing, div.cdk-overlay-backdrop.cdk-overlay-transparent-backdrop.cdk-overlay-backdrop-showing')
            for i in range(3):
                cnt = 0
                try:
                    cnt = await backdrop.count()
                except Exception:
                    cnt = 0
                if cnt and cnt > 0:
                    self.logger.info(f"[{self.req_id}] Detected transparent backdrops ({cnt}); sending ESC to close (attempt {i+1}/3).")
                    try:
                        await self.page.keyboard.press('Escape')
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass
                else:
                    break
        except Exception:
            pass

    async def _verify_chat_cleared(self, check_client_disconnected: Callable):
        """Verify chat cleared"""
        last_response_container = self.page.locator(RESPONSE_CONTAINER_SELECTOR).last
        await asyncio.sleep(0.5)
        await self._check_disconnect(check_client_disconnected, "After Clear Post-Delay")
        try:
            await expect_async(last_response_container).to_be_hidden(timeout=CLEAR_CHAT_VERIFY_TIMEOUT_MS - 500)
            self.logger.info(f"[{self.req_id}] ✅ Chat cleared (verification passed - last response container hidden).")
        except Exception as verify_err:
            self.logger.warning(f"[{self.req_id}] ⚠️ Warning: chat clear verification failed (last response container still visible): {verify_err}")
    
    # Removed direct setting of <input type=file> upload path; unified to menu-based upload

    async def _handle_post_upload_dialog(self):
        """Handle possible authorization/copyright dialogs after upload; prefer clicking agree-like buttons, avoid dismissing critical dialogs."""
        try:
            overlay_container = self.page.locator('div.cdk-overlay-container')
            if await overlay_container.count() == 0:
                return

            # Candidate agree button texts/labels
            agree_texts = [
                'Agree', 'I agree', 'Allow', 'Continue', 'OK',
                '确定', '同意', '继续', '允许'
            ]
            # Search visible buttons within overlay container
            for text in agree_texts:
                try:
                    btn = overlay_container.locator(f"button:has-text('{text}')")
                    if await btn.count() > 0 and await btn.first.is_visible(timeout=300):
                        await btn.first.click()
                        self.logger.info(f"[{self.req_id}] Post-upload dialog: clicked button '{text}'.")
                        await asyncio.sleep(0.3)
                        break
                except Exception:
                    continue
            # Copyright acknowledgment button via aria-label
            try:
                acknow_btn_locator = self.page.locator('button[aria-label*="copyright" i], button[aria-label*="acknowledge" i]')
                if await acknow_btn_locator.count() > 0 and await acknow_btn_locator.first.is_visible(timeout=300):
                    await acknow_btn_locator.first.click()
                    self.logger.info(f"[{self.req_id}] Post-upload dialog: clicked copyright acknowledgment (aria-label match).")
                    await asyncio.sleep(0.3)
            except Exception:
                pass

            # Wait for overlay to disappear (avoid forcing ESC)
            try:
                overlay_backdrop = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-backdrop-showing')
                if await overlay_backdrop.count() > 0:
                    try:
                        await expect_async(overlay_backdrop).to_be_hidden(timeout=3000)
                        self.logger.info(f"[{self.req_id}] Post-upload overlay backdrop hidden.")
                    except Exception:
                        self.logger.warning(f"[{self.req_id}] Post-upload overlay backdrop still present; subsequent submit may be intercepted.")
            except Exception:
                pass
        except Exception:
            pass

    async def _ensure_files_attached(self, wrapper_locator, expected_min: int = 1, timeout_ms: int = 5000) -> bool:
        """Poll input area to ensure file inputs/chips/blobs count >= expected_min."""
        end = asyncio.get_event_loop().time() + (timeout_ms / 1000)
        while asyncio.get_event_loop().time() < end:
            try:
                # NOTE: normalize JS eval string to avoid parser confusion
                counts = await wrapper_locator.evaluate("""
                    (el) => {
                      const result = {inputs:0, chips:0, blobs:0};
                      try { el.querySelectorAll('input[type="file"]').forEach(i => { result.inputs += (i.files ? i.files.length : 0); }); } catch(e){}
                      try { result.chips = el.querySelectorAll('button[aria-label*="Remove" i], button[aria-label*="asset" i]').length; } catch(e){}
                      try { result.blobs = el.querySelectorAll('img[src^="blob:"], video[src^="blob:"]').length; } catch(e){}
                      return result;
                    }
                    """)

                total = 0
                if isinstance(counts, dict):
                    total = max(int(counts.get('inputs') or 0), int(counts.get('chips') or 0), int(counts.get('blobs') or 0))
                if total >= expected_min:
                    self.logger.info(f"[{self.req_id}] Detected attached files: inputs={counts.get('inputs')}, chips={counts.get('chips')}, blobs={counts.get('blobs')} (>= {expected_min})")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
        self.logger.warning(f"[{self.req_id}] Did not detect attached files within timeout (expected >= {expected_min})")
        return False

    async def _open_upload_menu_and_choose_file(self, files_list: List[str]) -> bool:
        """Open 'Insert assets' menu, choose 'Upload File', and set files via hidden input or native chooser."""
        try:
            # If previous menu/dialog transparent backdrop lingers, try closing
            try:
                tb = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-transparent-backdrop.cdk-overlay-backdrop-showing')
                if await tb.count() > 0 and await tb.first.is_visible(timeout=300):
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.2)
            except Exception:
                pass

            trigger = self.page.locator('button[aria-label="Insert assets such as images, videos, files, or audio"]')
            await trigger.click()
            menu_container = self.page.locator('div.cdk-overlay-container')
            # Wait for menu to show
            try:
                await expect_async(menu_container.locator("div[role='menu']").first).to_be_visible(timeout=3000)
            except Exception:
                # Try to trigger again
                try:
                    await trigger.click()
                    await expect_async(menu_container.locator("div[role='menu']").first).to_be_visible(timeout=3000)
                except Exception:
                    self.logger.warning(f"[{self.req_id}] Failed to show upload menu panel.")
                    return False

            # Prefer aria-label='Upload File'
            try:
                upload_btn = menu_container.locator("div[role='menu'] button[role='menuitem'][aria-label='Upload File']")
                if await upload_btn.count() == 0:
                    # Fallback by text
                    upload_btn = menu_container.locator("div[role='menu'] button[role='menuitem']:has-text('Upload File')")
                if await upload_btn.count() == 0:
                    self.logger.warning(f"[{self.req_id}] 'Upload File' menu item not found.")
                    return False
                btn = upload_btn.first
                await expect_async(btn).to_be_visible(timeout=2000)
                # Prefer hidden input[type=file]
                input_loc = btn.locator('input[type="file"]')
                if await input_loc.count() > 0:
                    await input_loc.set_input_files(files_list)
                    self.logger.info(f"[{self.req_id}] ✅ Set files via hidden input in menu item (Upload File): {len(files_list)} files")
                else:
                    # Fallback native file chooser
                    async with self.page.expect_file_chooser() as fc_info:
                        await btn.click()
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(files_list)
                    self.logger.info(f"[{self.req_id}] ✅ Set files via native file chooser: {len(files_list)} files")
            except Exception as e_set:
                self.logger.error(f"[{self.req_id}] Failed setting files: {e_set}")
                return False
            # Close lingering menu backdrops
            try:
                backdrop = self.page.locator('div.cdk-overlay-backdrop.cdk-overlay-backdrop-showing, div.cdk-overlay-backdrop.cdk-overlay-transparent-backdrop.cdk-overlay-backdrop-showing')
                if await backdrop.count() > 0:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.2)
            except Exception:
                pass
            # Handle possible authorization popups
            await self._handle_post_upload_dialog()
            return True
        except Exception as e:
            self.logger.error(f"[{self.req_id}] Failed to set files via upload menu: {e}")
            return False

    async def submit_prompt(self, prompt: str,image_list: List, check_client_disconnected: Callable):
        """Submit prompt to the page."""
        self.logger.info(f"[{self.req_id}] Filling and submitting prompt ({len(prompt)} chars)...")
        prompt_textarea_locator = self.page.locator(PROMPT_TEXTAREA_SELECTOR)
        autosize_wrapper_locator = self.page.locator('ms-prompt-input-wrapper ms-autosize-textarea')
        submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)

        try:
            await expect_async(prompt_textarea_locator).to_be_visible(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "After Input Visible")

            # Fill text via JS
            await prompt_textarea_locator.evaluate(
                '''
                (element, text) => {
                    element.value = text;
                    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                }
                ''',
                prompt
            )
            await autosize_wrapper_locator.evaluate('(element, text) => { element.setAttribute("data-value", text); }', prompt)
            await self._check_disconnect(check_client_disconnected, "After Input Fill")

            # Uploads via menu + hidden input; handle possible authorization popups
            try:
                self.logger.info(f"[{self.req_id}] Attachments to upload: {len(image_list)}")
            except Exception:
                pass
            if len(image_list) > 0:
                ok = await self._open_upload_menu_and_choose_file(image_list)
                if not ok:
                    self.logger.error(f"[{self.req_id}] Error while uploading files: menu-based file setting failed")

            # If clear-chat confirmation overlay exists, handle it to avoid blocking submission
            try:
                overlay_locator = self.page.locator(OVERLAY_SELECTOR)
                if await overlay_locator.count() > 0:
                    self.logger.info(f"[{self.req_id}] Detected overlay; trying to click 'Discard and continue'...")
                    confirm_button_locator = self.page.locator(CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR)
                    try:
                        await expect_async(confirm_button_locator).to_be_visible(timeout=2000)
                        await confirm_button_locator.click(timeout=CLICK_TIMEOUT_MS)
                        self.logger.info(f"[{self.req_id}] ✅ Clicked 'Discard and continue'. Waiting for overlay to disappear...")
                        await expect_async(overlay_locator).to_be_hidden(timeout=5000)
                    except Exception as confirm_err:
                        self.logger.warning(f"[{self.req_id}] ⚠️ Failed to handle overlay confirmation or button not visible: {confirm_err}")
            except Exception:
                pass

            # Wait for submit button to be enabled
            wait_timeout_ms_submit_enabled = 100000
            try:
                await self._check_disconnect(check_client_disconnected, "After prompt fill, wait for submit enabled - pre-check")
                await expect_async(submit_button_locator).to_be_enabled(timeout=wait_timeout_ms_submit_enabled)
                self.logger.info(f"[{self.req_id}] ✅ Submit button enabled.")
            except Exception as e_pw_enabled:
                self.logger.error(f"[{self.req_id}] ❌ Timeout or error waiting for submit button enabled: {e_pw_enabled}")
                await save_error_snapshot(f"submit_button_enable_timeout_{self.req_id}")
                raise

            await self._check_disconnect(check_client_disconnected, "After Submit Button Enabled")
            self.logger.info(f"[{self.req_id}] Delaying 3s before clicking Run (1/2)...")
            await asyncio.sleep(3.0)
            try:
                await self._handle_post_upload_dialog()
            except Exception:
                pass
            try:
                if await submit_button_locator.is_enabled(timeout=1000):
                    await submit_button_locator.click(timeout=5000)
                    self.logger.info(f"[{self.req_id}] ✅ Run clicked.")
                else:
                    self.logger.info(f"[{self.req_id}] Run seems disabled before click; proceeding anyway.")
            except Exception as click_err:
                self.logger.error(f"[{self.req_id}] ❌ Run click failed: {click_err}")
                await save_error_snapshot(f"submit_button_click_fail_{self.req_id}")

            await self._check_disconnect(check_client_disconnected, "After Submit")

        except Exception as e_input_submit:
            self.logger.error(f"[{self.req_id}] Error during input and submit: {e_input_submit}")
            if not isinstance(e_input_submit, ClientDisconnectedError):
                await save_error_snapshot(f"input_submit_error_{self.req_id}")
            raise

    async def _simulate_drag_drop_files(self, target_locator, files_list: List[str]) -> None:
        """Inject local files via drag-drop events into target.
        Triggers dragenter/dragover/drop; no extra validation here to save time.
        """
        payloads = []
        for path in files_list:
            try:
                with open(path, 'rb') as f:
                    raw = f.read()
                b64 = base64.b64encode(raw).decode('ascii')
                mime, _ = mimetypes.guess_type(path)
                payloads.append({
                    'name': path.split('/')[-1],
                    'mime': mime or 'application/octet-stream',
                    'b64': b64,
                })
            except Exception as e:
                self.logger.warning(f"[{self.req_id}] Failed reading file for drag-drop; skipping: {path} - {e}")

        if not payloads:
            raise Exception("No files available for drag-drop")

        candidates = [
            target_locator,
            self.page.locator('ms-prompt-input-wrapper ms-autosize-textarea textarea'),
            self.page.locator('ms-prompt-input-wrapper ms-autosize-textarea'),
            self.page.locator('ms-prompt-input-wrapper'),
        ]

        last_err = None
        for idx, cand in enumerate(candidates):
            try:
                await expect_async(cand).to_be_visible(timeout=3000)
                await cand.evaluate(
                    """
                    (el, files) => {
                      const dt = new DataTransfer();
                      for (const p of files) {
                        const bstr = atob(p.b64);
                        const len = bstr.length;
                        const u8 = new Uint8Array(len);
                        for (let i = 0; i < len; i++) u8[i] = bstr.charCodeAt(i);
                        const blob = new Blob([u8], { type: p.mime || 'application/octet-stream' });
                        const file = new File([blob], p.name, { type: p.mime || 'application/octet-stream' });
                        dt.items.add(file);
                      }
                      const evEnter = new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt });
                      el.dispatchEvent(evEnter);
                      const evOver = new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt });
                      el.dispatchEvent(evOver);
                      const evDrop = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt });
                      el.dispatchEvent(evDrop);
                    }
                    """,
                    payloads
                )
                await asyncio.sleep(0.5)
                self.logger.info(f"[{self.req_id}] Drag-drop events fired on candidate {idx+1}/{len(candidates)}.")
                return
            except Exception as e_try:
                last_err = e_try
                continue

        # Fallback: try document.body
        try:
            await self.page.evaluate(
                """
                (files) => {
                  const dt = new DataTransfer();
                  for (const p of files) {
                    const bstr = atob(p.b64);
                    const len = bstr.length;
                    const u8 = new Uint8Array(len);
                    for (let i = 0; i < len; i++) u8[i] = bstr.charCodeAt(i);
                    const blob = new Blob([u8], { type: p.mime || 'application/octet-stream' });
                    const file = new File([blob], p.name, { type: p.mime || 'application/octet-stream' });
                    dt.items.add(file);
                  }
                  const el = document.body;
                  const evEnter = new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt });
                  el.dispatchEvent(evEnter);
                  const evOver = new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt });
                  el.dispatchEvent(evOver);
                  const evDrop = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt });
                  el.dispatchEvent(evDrop);
                }
                """,
                payloads
            )
            await asyncio.sleep(0.5)
            self.logger.info(f"[{self.req_id}] Drag-drop events fired on document.body (fallback).")
            return
        except Exception:
            pass

        raise last_err or Exception("Drag-drop did not fire on any candidate")


    async def _try_enter_submit(self, prompt_textarea_locator, check_client_disconnected: Callable) -> bool:
        """Prefer submitting via Enter key."""
        import os
        try:
            # Detect OS
            host_os_from_launcher = os.environ.get('HOST_OS_FOR_SHORTCUT')
            is_mac_determined = False

            if host_os_from_launcher == "Darwin":
                is_mac_determined = True
            elif host_os_from_launcher in ["Windows", "Linux"]:
                is_mac_determined = False
            else:
                # Use browser detection
                try:
                    user_agent_data_platform = await self.page.evaluate("() => navigator.userAgentData?.platform || ''")
                except Exception:
                    user_agent_string = await self.page.evaluate("() => navigator.userAgent || ''")
                    user_agent_string_lower = user_agent_string.lower()
                    if "macintosh" in user_agent_string_lower or "mac os x" in user_agent_string_lower:
                        user_agent_data_platform = "macOS"
                    else:
                        user_agent_data_platform = "Other"

                is_mac_determined = "mac" in user_agent_data_platform.lower()

            shortcut_modifier = "Meta" if is_mac_determined else "Control"
            shortcut_key = "Enter"

            await prompt_textarea_locator.focus(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "After Input Focus")
            await asyncio.sleep(0.1)

            # Record pre-submission content for verification
            original_content = ""
            try:
                original_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
            except Exception:
                # If cannot read original content, still attempt submission
                pass

            # Try Enter submission
            self.logger.info(f"[{self.req_id}] Attempting Enter key submission")
            try:
                await self.page.keyboard.press('Enter')
            except Exception:
                try:
                    await prompt_textarea_locator.press('Enter')
                except Exception:
                    pass

            await self._check_disconnect(check_client_disconnected, "After Enter Press")
            await asyncio.sleep(2.0)

            # Verify submission
            submission_success = False
            try:
                # Method 1: input cleared
                current_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
                if original_content and not current_content.strip():
                    self.logger.info(f"[{self.req_id}] Verification method 1: input cleared; Enter submit succeeded")
                    submission_success = True

                # Method 2: submit button disabled
                if not submission_success:
                    submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
                    try:
                        is_disabled = await submit_button_locator.is_disabled(timeout=2000)
                        if is_disabled:
                            self.logger.info(f"[{self.req_id}] Verification method 2: submit button disabled; Enter submit succeeded")
                            submission_success = True
                    except Exception:
                        pass

                # Method 3: response container appeared
                if not submission_success:
                    try:
                        response_container = self.page.locator(RESPONSE_CONTAINER_SELECTOR)
                        container_count = await response_container.count()
                        if container_count > 0:
                            last_container = response_container.last
                            if await last_container.is_visible(timeout=1000):
                                self.logger.info(f"[{self.req_id}] Verification method 3: response container detected; Enter submit succeeded")
                                submission_success = True
                    except Exception:
                        pass
            except Exception as verify_err:
                self.logger.warning(f"[{self.req_id}] Error during Enter submit verification: {verify_err}")
                submission_success = True

            if submission_success:
                self.logger.info(f"[{self.req_id}] ✅ Enter submit succeeded")
                return True
            else:
                self.logger.warning(f"[{self.req_id}] ⚠️ Enter submit verification failed")
                return False
        except Exception as shortcut_err:
            self.logger.warning(f"[{self.req_id}] Enter submit failed: {shortcut_err}")
            return False

    async def _try_combo_submit(self, prompt_textarea_locator, check_client_disconnected: Callable) -> bool:
        """Try combo submission (Meta/Control + Enter)."""
        import os
        try:
            host_os_from_launcher = os.environ.get('HOST_OS_FOR_SHORTCUT')
            is_mac_determined = False
            if host_os_from_launcher == "Darwin":
                is_mac_determined = True
            elif host_os_from_launcher in ["Windows", "Linux"]:
                is_mac_determined = False
            else:
                try:
                    user_agent_data_platform = await self.page.evaluate("() => navigator.userAgentData?.platform || ''")
                except Exception:
                    user_agent_string = await self.page.evaluate("() => navigator.userAgent || ''")
                    user_agent_string_lower = user_agent_string.lower()
                    if "macintosh" in user_agent_string_lower or "mac os x" in user_agent_string_lower:
                        user_agent_data_platform = "macOS"
                    else:
                        user_agent_data_platform = "Other"
                is_mac_determined = "mac" in user_agent_data_platform.lower()

            shortcut_modifier = "Meta" if is_mac_determined else "Control"
            shortcut_key = "Enter"

            await prompt_textarea_locator.focus(timeout=5000)
            await self._check_disconnect(check_client_disconnected, "After Input Focus")
            await asyncio.sleep(0.1)

            # Record pre-submission content for verification
            original_content = ""
            try:
                original_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
            except Exception:
                pass

            self.logger.info(f"[{self.req_id}] Attempting combo submission: {shortcut_modifier}+{shortcut_key}")
            try:
                await self.page.keyboard.press(f'{shortcut_modifier}+{shortcut_key}')
            except Exception:
                try:
                    await self.page.keyboard.down(shortcut_modifier)
                    await asyncio.sleep(0.05)
                    await self.page.keyboard.press(shortcut_key)
                    await asyncio.sleep(0.05)
                    await self.page.keyboard.up(shortcut_modifier)
                except Exception:
                    pass

            await self._check_disconnect(check_client_disconnected, "After Combo Press")
            await asyncio.sleep(2.0)

            submission_success = False
            try:
                current_content = await prompt_textarea_locator.input_value(timeout=2000) or ""
                if original_content and not current_content.strip():
                    self.logger.info(f"[{self.req_id}] Verification method 1: input cleared; combo submit succeeded")
                    submission_success = True
                if not submission_success:
                    submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
                    try:
                        is_disabled = await submit_button_locator.is_disabled(timeout=2000)
                        if is_disabled:
                            self.logger.info(f"[{self.req_id}] Verification method 2: submit button disabled; combo submit succeeded")
                            submission_success = True
                    except Exception:
                        pass
                if not submission_success:
                    try:
                        response_container = self.page.locator(RESPONSE_CONTAINER_SELECTOR)
                        container_count = await response_container.count()
                        if container_count > 0:
                            last_container = response_container.last
                            if await last_container.is_visible(timeout=1000):
                                self.logger.info(f"[{self.req_id}] Verification method 3: response container detected; combo submit succeeded")
                                submission_success = True
                    except Exception:
                        pass
            except Exception as verify_err:
                self.logger.warning(f"[{self.req_id}] Error during combo submit verification: {verify_err}")
                submission_success = True

            if submission_success:
                self.logger.info(f"[{self.req_id}] ✅ Combo submit succeeded")
                return True
            else:
                self.logger.warning(f"[{self.req_id}] ⚠️ Combo submit verification failed")
                return False
        except Exception as combo_err:
            self.logger.warning(f"[{self.req_id}] Combo submit failed: {combo_err}")
            return False

    async def get_response(self, check_client_disconnected: Callable) -> str:
        """Get final response content."""
        self.logger.info(f"[{self.req_id}] Waiting for and fetching response...")

        try:
            # Wait for response container to appear
            response_container_locator = self.page.locator(RESPONSE_CONTAINER_SELECTOR).last
            response_element_locator = response_container_locator.locator(RESPONSE_TEXT_SELECTOR)

            self.logger.info(f"[{self.req_id}] Waiting for response element to be attached to DOM...")
            await expect_async(response_element_locator).to_be_attached(timeout=90000)
            await self._check_disconnect(check_client_disconnected, "Get response - response element attached")

            # Wait for response completion
            submit_button_locator = self.page.locator(SUBMIT_BUTTON_SELECTOR)
            edit_button_locator = self.page.locator(EDIT_MESSAGE_BUTTON_SELECTOR)
            input_field_locator = self.page.locator(PROMPT_TEXTAREA_SELECTOR)

            self.logger.info(f"[{self.req_id}] Waiting for response completion...")
            completion_detected = await _wait_for_response_completion(
                self.page, input_field_locator, submit_button_locator, edit_button_locator, self.req_id, check_client_disconnected, None
            )

            if not completion_detected:
                self.logger.warning(f"[{self.req_id}] Response completion detection failed; attempting to get current content")
            else:
                self.logger.info(f"[{self.req_id}] ✅ Response completion detected")

            # Fetch final content
            final_content = await _get_final_response_content(self.page, self.req_id, check_client_disconnected)

            if not final_content or not final_content.strip():
                self.logger.warning(f"[{self.req_id}] ⚠️ Final response is empty")
                await save_error_snapshot(f"empty_response_{self.req_id}")
                # Do not throw; return empty content for upstream handling
                return ""

            self.logger.info(f"[{self.req_id}] ✅ Successfully fetched response ({len(final_content)} chars)")
            return final_content

        except Exception as e:
            self.logger.error(f"[{self.req_id}] ❌ Error while getting response: {e}")
            if not isinstance(e, ClientDisconnectedError):
                await save_error_snapshot(f"get_response_error_{self.req_id}")
            raise
