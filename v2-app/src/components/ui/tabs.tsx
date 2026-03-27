import * as React from "react"
import { cn } from "@/lib/utils"

const TabsContext = React.createContext<{ activeValue: string; onTabChange: (val: string) => void }>({
    activeValue: "",
    onTabChange: () => { },
})

const Tabs = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement> & { value: string; onValueChange: (val: string) => void }
>(({ className, value, onValueChange, children, ...props }, ref) => (
    <TabsContext.Provider value={{ activeValue: value, onTabChange: onValueChange }}>
        <div ref={ref} className={cn("w-full", className)} {...props}>
            {children}
        </div>
    </TabsContext.Provider>
))
Tabs.displayName = "Tabs"

const TabsList = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => (
    <div className="w-full overflow-x-auto pb-1">
        <div
            ref={ref}
            className={cn(
                "inline-flex h-12 items-center gap-1 rounded-xl bg-gray-100/80 dark:bg-[#111318] p-1 text-gray-500 dark:text-gray-400 w-max md:w-fit border border-gray-200/60 dark:border-[#1e2028]",
                className
            )}
            {...props}
        >
            {children}
        </div>
    </div>
))
TabsList.displayName = "TabsList"

const TabsTrigger = React.forwardRef<
    HTMLButtonElement,
    React.ButtonHTMLAttributes<HTMLButtonElement> & { value: string }
>(({ className, value, ...props }, ref) => {
    const { activeValue, onTabChange } = React.useContext(TabsContext)
    const isActive = value === activeValue
    return (
        <button
            ref={ref}
            type="button"
            onClick={() => onTabChange(value)}
            className={cn(
                "inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-lg px-5 py-2 text-[13px] sm:px-6 sm:py-2 sm:text-sm font-semibold transition-all duration-200 cursor-pointer",
                isActive
                    ? "bg-white dark:bg-[#22252e] text-zinc-900 dark:text-white shadow-md border border-gray-200/50 dark:border-[#3a3e47]"
                    : "text-gray-500 dark:text-gray-500 hover:text-zinc-800 dark:hover:text-gray-200 hover:bg-white/50 dark:hover:bg-[#1c1f26]/60",
                className
            )}
            {...props}
        />
    )
})
TabsTrigger.displayName = "TabsTrigger"

const TabsContent = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement> & { value: string }
>(({ className, value, ...props }, ref) => {
    const { activeValue } = React.useContext(TabsContext)
    if (value !== activeValue) return null
    return (
        <div
            ref={ref}
            className={cn(
                "mt-6 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 animate-in fade-in duration-300",
                className
            )}
            {...props}
        />
    )
})
TabsContent.displayName = "TabsContent"

export { Tabs, TabsList, TabsTrigger, TabsContent }
