import "@testing-library/jest-dom/vitest";

// jsdom does not implement scrolling APIs used by the chat panel.
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = function scrollTo() {};
