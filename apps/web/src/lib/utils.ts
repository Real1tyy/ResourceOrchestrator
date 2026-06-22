import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// The standard shadcn/ui class-merge helper — ready for when components land.
export function cn(...inputs: ClassValue[]): string {
    return twMerge(clsx(inputs));
}
