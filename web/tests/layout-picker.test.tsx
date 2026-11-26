import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LayoutPicker, layoutOf } from "@/components/LayoutPicker";
import { LangProvider } from "@/lib/i18n";
import { clip } from "./helpers";

const withMeta = (meta: Record<string, unknown>) => ({ ...clip("c1"), render_meta: meta });

describe("clip layout picker", () => {
  it("shows five layouts, marks the owner's pick and one tap sends exactly that layout", () => {
    const onPick = vi.fn();
    render(<LangProvider initial="en"><LayoutPicker clip={withMeta({ layout: "split" })} busy={false} onPick={onPick} /></LangProvider>);
    const tiles = screen.getAllByRole("radio");
    expect(tiles).toHaveLength(5);
    expect(screen.getByRole("radio", { name: /Split/ }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("radio", { name: /Split/ }));
    expect(onPick).not.toHaveBeenCalled();                      // the current pick is not a new version
    fireEvent.click(screen.getByRole("radio", { name: /Reaction/ }));
    expect(onPick).toHaveBeenCalledWith("reaction");
  });

  it("auto is the default, the suggestion and its reason are shown, a fallback is explained in plain words", () => {
    render(<LangProvider initial="en"><LayoutPicker clip={withMeta({ layout_suggested: { layout: "split", why: "Two people in the same shot." }, layout_fallback: "the content card would cover the face" })} busy={false} onPick={() => undefined} /></LangProvider>);
    expect(screen.getByRole("radio", { name: /Auto/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("layout-suggested").textContent).toContain("Two people in the same shot.");
    expect(screen.getByTestId("layout-fallback").textContent).toContain("content card");
  });

  it("speaks Arabic and ignores values it does not know", () => {
    render(<LangProvider initial="ar"><LayoutPicker clip={withMeta({ layout: "diagonal" })} busy={true} onPick={() => undefined} /></LangProvider>);
    expect(screen.getByRole("radio", { name: /شاشة منقسمة/ })).toBeTruthy();
    expect(layoutOf(withMeta({ layout: "diagonal" })).picked).toBe("auto");
    expect((screen.getAllByRole("radio")[0] as HTMLButtonElement).disabled).toBe(true);
  });
});
