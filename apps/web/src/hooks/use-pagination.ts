"use client";

import { useCallback, useState } from "react";

export interface PaginationState {
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  reset: () => void;
}

export function usePagination(defaultPageSize = 20): PaginationState {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeRaw] = useState(defaultPageSize);

  const setPageSize = useCallback((size: number) => {
    setPageSizeRaw(size);
    setPage(1); // reset to first page on size change
  }, []);

  const reset = useCallback(() => {
    setPage(1);
    setPageSizeRaw(defaultPageSize);
  }, [defaultPageSize]);

  return { page, pageSize, setPage, setPageSize, reset };
}
