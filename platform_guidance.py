from __future__ import annotations

"""Dismiss transient guided-tour overlays on the teaching platform."""

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By


GUIDANCE_LABELS = {"下一步", "我知道了"}
GUIDANCE_SELECTORS = (
    "button,[role='button'],a,.driver-popover-next-btn,.introjs-nextbutton,"
    ".el-tour__next-btn"
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


def dismiss_platform_guidance(driver) -> bool:
    """Click one visible guide control and return whether anything was dismissed."""
    try:
        current_url = driver.current_url
        if not isinstance(current_url, str):
            return False
        url = current_url.casefold()
        if "aic.sysu.edu.cn" not in url:
            return False
        if any(marker in url for marker in ("login", "auth", "cas", "sso")):
            return False
        for element in driver.find_elements(By.CSS_SELECTOR, GUIDANCE_SELECTORS):
            try:
                label = _normalize(element.text)
                if label not in GUIDANCE_LABELS:
                    continue
                if not element.is_displayed() or not element.is_enabled():
                    continue
                if element.get_attribute("data-codex-guide-dismissed") == "1":
                    continue
                if not _is_guidance_context(driver, element, label):
                    continue
                driver.execute_script(
                    "arguments[0].setAttribute('data-codex-guide-dismissed','1');"
                    "arguments[0].click();",
                    element,
                )
                return True
            except WebDriverException:
                continue
    except WebDriverException:
        return False
    return False


def install_guidance_hook(driver) -> None:
    """Run guide dismissal before every responsive Selenium wait poll."""
    driver._responsive_wait_hook = lambda: dismiss_platform_guidance(driver)
