import {
    ColumnDef,
    flexRender,
    getCoreRowModel,
    useReactTable,
    getPaginationRowModel,
} from "@tanstack/react-table"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { Select } from "./select"

interface DataTableProps<TData, TValue> {
    columns: ColumnDef<TData, TValue>[]
    data: TData[]
    /** 服务端分页：当前页（从 1 开始） */
    page?: number
    /** 服务端分页：总页数 */
    totalPages?: number
    /** 服务端分页：总条数 */
    total?: number
    /** 服务端分页：翻页回调 */
    onPageChange?: (page: number) => void
    /** 服务端分页：每页条数 */
    pageSize?: number
    /** 服务端分页：每页条数选项 */
    pageSizeOptions?: number[]
    /** 服务端分页：切换每页条数 */
    onPageSizeChange?: (size: number) => void
}

export function DataTable<TData, TValue>({
    columns,
    data,
    page,
    totalPages,
    total,
    onPageChange,
    pageSize,
    pageSizeOptions = [10, 20, 50, 100],
    onPageSizeChange,
}: DataTableProps<TData, TValue>) {
    const isServerPagination = page !== undefined && totalPages !== undefined && onPageChange !== undefined

    const table = useReactTable({
        data,
        columns,
        getCoreRowModel: getCoreRowModel(),
        ...(!isServerPagination ? { getPaginationRowModel: getPaginationRowModel() } : { manualPagination: true }),
    })

    return (
        <div className="w-full">
            <div className="rounded-xl border border-gray-100 dark:border-[#2a2d35] bg-white dark:bg-[#181a20] elegant-card shadow-sm">
                <div className="overflow-x-auto rounded-t-xl">
                    <table className="w-full text-sm text-left relative">
                        <thead className="bg-[#f8fafc] dark:bg-[#1c1f26] border-b border-gray-100 dark:border-[#2a2d35]">
                            {table.getHeaderGroups().map((headerGroup) => (
                                <tr key={headerGroup.id}>
                                    {headerGroup.headers.map((header) => {
                                        return (
                                            <th
                                                key={header.id}
                                                className="h-12 px-6 text-left align-middle font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider text-[11px] whitespace-nowrap"
                                            >
                                                {header.isPlaceholder
                                                    ? null
                                                    : flexRender(
                                                        header.column.columnDef.header,
                                                        header.getContext()
                                                    )}
                                            </th>
                                        )
                                    })}
                                </tr>
                            ))}
                        </thead>
                        <tbody className="divide-y divide-gray-50 dark:divide-[#2a2d35]">
                            {table.getRowModel().rows?.length ? (
                                table.getRowModel().rows.map((row) => (
                                    <tr
                                        key={row.id}
                                        data-state={row.getIsSelected() && "selected"}
                                        className="hover-row hover:bg-[#f8fafc] dark:hover:bg-[#22252e] transition-colors"
                                    >
                                        {row.getVisibleCells().map((cell) => (
                                            <td key={cell.id} className="p-6 align-middle">
                                                {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                            </td>
                                        ))}
                                    </tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={columns.length} className="h-24 text-center text-gray-500">
                                        暂无数据。
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
                {/* Pagination */}
                {isServerPagination ? (
                    <div className="flex items-center justify-between px-6 py-3 border-t border-gray-100 dark:border-[#2a2d35] bg-white dark:bg-[#181a20]">
                        <div className="flex items-center gap-4 text-sm text-gray-500">
                            <span>第 {page} 页，共 {totalPages} 页{total !== undefined && `（${total} 条）`}</span>
                            {onPageSizeChange && pageSize && (
                                <span className="flex items-center gap-1.5">
                                    每页
                                    <Select
                                        value={String(pageSize)}
                                        onChange={(val) => onPageSizeChange(Number(val))}
                                        options={pageSizeOptions.map((s) => ({ value: String(s), label: `${s}` }))}
                                        className="w-[72px] [&_button]:h-8 [&_button]:text-xs [&_button]:rounded-lg"
                                    />
                                    条
                                </span>
                            )}
                        </div>
                        <div className="flex items-center space-x-2">
                            <button
                                onClick={() => onPageChange(Math.max(1, page - 1))}
                                disabled={page <= 1}
                                className="p-2 border border-gray-200 dark:border-[#2a2d35] rounded-lg disabled:opacity-50 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors dark:text-gray-300"
                            >
                                <ChevronLeft className="w-4 h-4" />
                            </button>
                            <button
                                onClick={() => onPageChange(Math.min(totalPages, page + 1))}
                                disabled={page >= totalPages}
                                className="p-2 border border-gray-200 dark:border-[#2a2d35] rounded-lg disabled:opacity-50 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors dark:text-gray-300"
                            >
                                <ChevronRight className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="flex items-center justify-between px-6 py-3 border-t border-gray-100 dark:border-[#2a2d35] bg-white dark:bg-[#181a20]">
                        <div className="text-sm text-gray-500">
                            第 {table.getState().pagination.pageIndex + 1} 页，共{" "}
                            {table.getPageCount()} 页
                        </div>
                        <div className="flex items-center space-x-2">
                            <button
                                onClick={() => table.previousPage()}
                                disabled={!table.getCanPreviousPage()}
                                className="p-2 border border-gray-200 dark:border-[#2a2d35] rounded-lg disabled:opacity-50 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors dark:text-gray-300"
                            >
                                <ChevronLeft className="w-4 h-4" />
                            </button>
                            <button
                                onClick={() => table.nextPage()}
                                disabled={!table.getCanNextPage()}
                                className="p-2 border border-gray-200 dark:border-[#2a2d35] rounded-lg disabled:opacity-50 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors dark:text-gray-300"
                            >
                                <ChevronRight className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
