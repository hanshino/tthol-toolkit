// Focusing a number field (or one marked data-select-all) selects its whole
// value, so a default is replaced by typing instead of edited around.
//
// A mouse click focuses on mousedown and then places the caret on mouseup,
// which would undo the selection; that one mouseup is swallowed. A second
// click in the already focused field places the caret as usual.

const SELECTOR = 'input[type="number"], input[data-select-all]';

export function installSelectOnFocus(): void {
  let fresh: HTMLInputElement | null = null;

  document.addEventListener('focusin', e => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement) || !el.matches(SELECTOR) || el.readOnly) return;
    fresh = el;
    el.select();
  });

  document.addEventListener('mouseup', e => {
    if (fresh && e.target === fresh) e.preventDefault();
    fresh = null;
  });

  // Keyboard focus never sees a mouseup; forget the field once it is left.
  document.addEventListener('focusout', () => { fresh = null; });
}
