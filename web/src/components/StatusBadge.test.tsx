import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["cached", "cached"],
    ["pending", "needs audio"],
    ["queued", "queued"],
    ["generating", "generating"],
    ["failed", "failed"],
  ] as const)("renders %s", (status, label) => {
    render(<StatusBadge status={status} title="why" />);
    const badge = screen.getByText(label);
    expect(badge).toHaveAttribute("data-status", status);
    expect(badge).toHaveClass(`badge-${status}`);
  });
});
