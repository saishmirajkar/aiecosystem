import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Category, Department, Location, Page, Product, Role, Supplier, Unit, User, Warehouse } from "@/types/api";

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

const long = { staleTime: 5 * 60_000 };
export const useWarehouses = (includeInactive = false) =>
  useQuery({ queryKey: ["warehouses", includeInactive], queryFn: () => api.get<Warehouse[]>("/warehouses", { include_inactive: includeInactive }), ...long });
export const useLocations = (warehouseId?: string) =>
  useQuery({ queryKey: ["locations", warehouseId], queryFn: () => api.get<Location[]>(`/warehouses/${warehouseId}/locations`), enabled: !!warehouseId, ...long });
export const useUnits = () => useQuery({ queryKey: ["units"], queryFn: () => api.get<Unit[]>("/units"), ...long });
export const useCategories = () => useQuery({ queryKey: ["categories"], queryFn: () => api.get<Category[]>("/categories"), ...long });
export const useDepartments = () => useQuery({ queryKey: ["departments"], queryFn: () => api.get<Department[]>("/departments"), ...long });
export const useRoles = () => useQuery({ queryKey: ["roles"], queryFn: () => api.get<Role[]>("/roles"), ...long });
export const useSuppliers = () =>
  useQuery({ queryKey: ["suppliers", "all"], queryFn: () => api.get<Page<Supplier>>("/suppliers", { page_size: 200 }), ...long });
export const useUsers = (enabled = true) =>
  useQuery({ queryKey: ["users", "all"], queryFn: () => api.get<Page<User>>("/users", { page_size: 200 }), enabled, ...long });
export const useProductSearch = (q: string) =>
  useQuery({ queryKey: ["products", "search", q], queryFn: () => api.get<Page<Product>>("/products", { q, page_size: 20 }), staleTime: 30_000 });
