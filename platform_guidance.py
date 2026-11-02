from __future__ import annotations

"""Dismiss transient guided-tour overlays on the teaching platform."""

import time

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from responsive_wait import responsive_sleep


GUIDANCE_LABELS = {"下一步", "我知道了"}
GUIDANCE_XPATH = (
    "//*[self::button or self::a or self::div or self::span or @role='button']"
    "[normalize-space(.)='下一步' or normalize-space(.)='我知道了']"
    "[not(.//*[self::button or self::a or self::div or self::span or @role='button']"
    "[normalize-space(.)='下一步' or normalize-space(.)='我知道了'])]"
)


def _normalize(value: str | None) -> str:
    return " ".join((value or "").split())


def _is_guidance_context(driver, element, label: str) -> bool:
    if label == "我知道了":
        return True
    return bool(
        driver.execute_script(
            """
            const element = arguments[0];
            const selector = [
              '[role="dialog"]', '.driver-popover', '.introjs-tooltip',
              '.el-tour', '.el-tour__content', '.shepherd-element',
              '[class*="intro"]', '[class*="Intro"]',
              '[class*="guide"]', '[class*="Guide"]',
              '[class*="tour"]', '[class*="Tour"]',
              '[class*="popover"]', '[class*="Popover"]'
            ].join(',');
            if (element.closest(selector)) return true;
            let node = element;
            while (node && node !== document.body) {
              const style = getComputedStyle(node);
              const z = Number.parseInt(style.zIndex, 10);
              if (style.position === 'fixed' && Number.isFinite(z) && z >= 1000) {
                return true;
              }
              node = node.parentElement;
            }
            return false;
            """,
            element,
        )
    )


def _dismiss_one_platform_guidance(driver) -> bool:
    """Find current guide controls again after every click; tours may reuse nodes."""
    try:
        current_url = driver.current_url
        if not isinstance(current_url, str):
            return False
        url = current_url.casefold()
        if "aic.sysu.edu.cn" not in url:
            return False
        if any(marker in url for marker in ("login", "auth", "cas", "sso")):
            return False
        for element in driver.find_elements(By.XPATH, GUIDANCE_XPATH):
            try:
                label = _normalize(element.text)
                if label not in GUIDANCE_LABELS:
                    continue
                if not element.is_displayed() or not element.is_enabled():
                    continue
                if not _is_guidance_context(driver, element, label):
                    continue
                # 不永久标记按钮：同一 DOM 节点可能负责连续多个“下一步”。
                driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                continue
    except WebDriverException:
        return False
    return False


def dismiss_platform_guidance(driver, *, timeout: float = 8, max_steps: int = 30) -> bool:
    """Advance successive tours, allowing animations and bounding stuck controls."""
    deadline = time.monotonic() + timeout
    dismissed = False
    quiet_since = None
    steps = 0
    while steps < max_steps and time.monotonic() < deadline:
        if _dismiss_one_platform_guidance(driver):
            dismissed = True
            steps += 1
            quiet_since = None
        elif not dismissed:
            return False
        else:
            if quiet_since is None:
                quiet_since = time.monotonic()
            if time.monotonic() - quiet_since >= 0.4:
                return True
        responsive_sleep(0.15)
    return dismissed


def install_guidance_hook(driver) -> None:
    """Run guide dismissal before every responsive Selenium wait poll."""
    driver._responsive_wait_hook = lambda: _dismiss_one_platform_guidance(driver)
