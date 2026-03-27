import * as React from "react"
import { cn } from "@/lib/utils"

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: "default" | "primary" | "outline" | "ghost" | "link" | "danger"
    size?: "default" | "sm" | "lg" | "icon"
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant = "default", size = "default", ...props }, ref) => {
        return (
            <button
                ref={ref}
                className={cn(
                    "inline-flex items-center justify-center whitespace-nowrap rounded-xl text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
                    {
                        "bg-zinc-900 text-white shadow-md hover:bg-zinc-800 dark:bg-white dark:text-zinc-900 dark:hover:bg-gray-200": variant === "default",
                        "bg-blue-600 text-white shadow-md shadow-blue-600/20 hover:bg-blue-700 dark:shadow-none": variant === "primary",
                        "border border-gray-200 bg-white shadow-sm hover:bg-gray-50 text-zinc-900 dark:border-[#2a2d35] dark:bg-[#181a20] dark:text-gray-200 dark:hover:bg-[#22252e]": variant === "outline",
                        "hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-[#22252e] dark:hover:text-gray-50": variant === "ghost",
                        "bg-red-600 text-white hover:bg-red-700": variant === "danger",
                        "underline-offset-4 hover:underline text-blue-600 dark:text-blue-400": variant === "link",
                        "h-9 px-4 py-2": size === "default",
                        "h-8 rounded-lg px-3 text-xs": size === "sm",
                        "h-10 rounded-xl px-8": size === "lg",
                        "h-9 w-9": size === "icon",
                    },
                    className
                )}
                {...props}
            />
        )
    }
)
Button.displayName = "Button"

export { Button }
