import { createBrowserRouter } from "react-router-dom";
import { ShopperProvider } from "./session-provider";
import { MarketplaceLayout } from "../components/layout";
import { HomePage, SearchPage, CategoryPage, ProductPage, ComparePage, CartPage } from "../pages";

export const router = createBrowserRouter([{ path: "/", element: <ShopperProvider><MarketplaceLayout /></ShopperProvider>, children: [{ index: true, element: <HomePage /> }, { path: "search", element: <SearchPage /> }, { path: "category/:category", element: <CategoryPage /> }, { path: "product/:entryId", element: <ProductPage /> }, { path: "compare", element: <ComparePage /> }, { path: "cart", element: <CartPage /> }] }]);
