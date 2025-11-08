#!/usr/bin/env python3
"""Test script to check imports for scroll endpoints"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    print("Testing imports...")
    
    # Test basic imports
    import logging
    from fastapi import Depends, HTTPException
    from fastapi.responses import JSONResponse
    import random
    
    print("Basic imports successful")
    
    # Test dependency imports
    from api_utils.dependencies import get_logger, get_page_instance
    print("Dependency imports successful")
    
    # Test PageController import
    from browser_utils.page_controller import PageController
    print("PageController import successful")
    
    # Test getting page instance
    page_instance = get_page_instance()
    if page_instance:
        print(f"Page instance available: {type(page_instance)}")
        print(f"  - Is closed: {page_instance.is_closed()}")
    else:
        print("Page instance is None")
    
    print("All imports successful!")
    
except Exception as e:
    print(f"Import error: {e}")
    import traceback
    traceback.print_exc()