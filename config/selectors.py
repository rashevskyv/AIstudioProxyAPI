"""
CSS Selector Configuration Module
Contains all CSS selectors used for page element location
"""

# --- Input-related Selectors ---
PROMPT_TEXTAREA_SELECTOR = 'ms-prompt-input-wrapper ms-autosize-textarea textarea'
INPUT_SELECTOR = PROMPT_TEXTAREA_SELECTOR
INPUT_SELECTOR2 = PROMPT_TEXTAREA_SELECTOR

# --- Button Selectors ---
# Submit button: prioritize matching aria-label="Run" button; fallback to container's submit button if page structure changes
SUBMIT_BUTTON_SELECTOR = 'button[aria-label="Run"].run-button, ms-run-button button[type="submit"].run-button'
CLEAR_CHAT_BUTTON_SELECTOR = 'button[data-test-clear="outside"][aria-label="New chat"]'
CLEAR_CHAT_CONFIRM_BUTTON_SELECTOR = 'button.ms-button-primary:has-text("Discard and continue")'
UPLOAD_BUTTON_SELECTOR = 'button[aria-label^="Insert assets"]'

# --- Response-related Selectors ---
RESPONSE_CONTAINER_SELECTOR = 'ms-chat-turn .chat-turn-container.model'
RESPONSE_TEXT_SELECTOR = 'ms-cmark-node.cmark-node'

# --- Loading and Status Selectors ---
LOADING_SPINNER_SELECTOR = 'button[aria-label="Run"].run-button svg .stoppable-spinner'
OVERLAY_SELECTOR = '.mat-mdc-dialog-inner-container'

# --- Error Notification Selectors ---
ERROR_TOAST_SELECTOR = 'div.toast.warning, div.toast.error'

# --- Edit-related Selectors ---
EDIT_MESSAGE_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button'
MESSAGE_TEXTAREA_SELECTOR = 'ms-chat-turn:last-child ms-text-chunk ms-autosize-textarea'
FINISH_EDIT_BUTTON_SELECTOR = 'ms-chat-turn:last-child .actions-container button.toggle-edit-button[aria-label="Stop editing"]'

# --- Menu and Copy-related Selectors ---
MORE_OPTIONS_BUTTON_SELECTOR = 'div.actions-container div ms-chat-turn-options div > button'
COPY_MARKDOWN_BUTTON_SELECTOR = 'button.mat-mdc-menu-item:nth-child(4)'
COPY_MARKDOWN_BUTTON_SELECTOR_ALT = 'div[role="menu"] button:has-text("Copy Markdown")'

# --- Settings-related Selectors ---
MAX_OUTPUT_TOKENS_SELECTOR = 'input[aria-label="Maximum output tokens"]'
STOP_SEQUENCE_INPUT_SELECTOR = 'input[aria-label="Add stop token"]'
MAT_CHIP_REMOVE_BUTTON_SELECTOR = 'mat-chip-set mat-chip-row button[aria-label*="Remove"]'
TOP_P_INPUT_SELECTOR = 'ms-slider input[type="number"][max="1"]'
TEMPERATURE_INPUT_SELECTOR = 'ms-slider input[type="number"][max="2"]'
USE_URL_CONTEXT_SELECTOR = 'button[aria-label="Browse the url context"]'

# --- Thinking Mode-related Selectors ---
# Main thinking toggle: controls whether to enable thinking mode (master switch)
ENABLE_THINKING_MODE_TOGGLE_SELECTOR = '[data-test-toggle="enable-thinking"] button'
# Manual budget toggle: controls whether to manually limit thinking budget
SET_THINKING_BUDGET_TOGGLE_SELECTOR = '[data-test-toggle="manual-budget"] button'
# Thinking budget input field
THINKING_BUDGET_INPUT_SELECTOR = '[data-test-slider] input[type="number"]'

# --- Google Search Grounding ---
GROUNDING_WITH_GOOGLE_SEARCH_TOGGLE_SELECTOR = 'div[data-test-id="searchAsAToolTooltip"] mat-slide-toggle button'
