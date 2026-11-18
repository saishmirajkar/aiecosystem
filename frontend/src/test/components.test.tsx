import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/page";

describe("DataTable", () => {
  const cols = [{ key: "n", header: "Name", cell: (r: { id: string; name: string }) => r.name, sortKey: "name" }];
  it("shows empty state", () => {
    render(<DataTable columns={cols} rows={[]} rowKey={(r) => r.id} />);
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
  });
  it("renders rows, sorts and paginates", async () => {
    const onSort = vi.fn();
    const onPage = vi.fn();
    render(<DataTable columns={cols} rows={[{ id: "1", name: "Steel" }]} rowKey={(r) => r.id} sort="name" onSort={onSort} page={1} pageSize={1} total={3} onPage={onPage} />);
    expect(screen.getByText("Steel")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Name/ }));
    expect(onSort).toHaveBeenCalledWith("-name");
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPage).toHaveBeenCalledWith(2);
  });
});

describe("StatusBadge", () => {
  it("labels out-of-stock clearly", () => {
    render(<StatusBadge status="out" />);
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
  });
});
