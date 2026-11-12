import pandas as pd

def process_data(source_dfs):
    columns = ['子类别', 'Q1', 'Q2', 'Q3', 'Q4', '全年合计', '销量排名', '销量占比']

    def empty_result():
        return pd.DataFrame(columns=columns)

    if not isinstance(source_dfs, dict) or not source_dfs:
        return empty_result()

    def get_table(name):
        for key in source_dfs:
            key_str = str(key)
            if key_str == name or key_str.endswith('_' + name):
                return source_dfs[key].copy()
        for key in source_dfs:
            if name.lower() in str(key).lower():
                return source_dfs[key].copy()
        return pd.DataFrame()

    header = get_table('OrderHeader')
    detail = get_table('OrderDetail')
    product = get_table('Product')
    subcategory = get_table('Subcategory')

    if header.empty or detail.empty or product.empty or subcategory.empty:
        return empty_result()

    if any(c not in header.columns for c in ['SalesOrderID', 'OrderDate']):
        return empty_result()
    if any(c not in detail.columns for c in ['SalesOrderID', 'ProductID', 'OrderQty']):
        return empty_result()
    if any(c not in product.columns for c in ['ProductID', 'ProductSubcategoryID']):
        return empty_result()
    if any(c not in subcategory.columns for c in ['ProductSubcategoryID', 'Name']):
        return empty_result()

    header['SalesOrderID'] = pd.to_numeric(header['SalesOrderID'], errors='coerce')
    header['OrderDate'] = pd.to_datetime(header['OrderDate'], errors='coerce')
    header['_quarter'] = header['OrderDate'].dt.quarter

    detail['SalesOrderID'] = pd.to_numeric(detail['SalesOrderID'], errors='coerce')
    detail['ProductID'] = pd.to_numeric(detail['ProductID'], errors='coerce')
    detail['OrderQty'] = pd.to_numeric(detail['OrderQty'], errors='coerce').fillna(0)

    product['ProductID'] = pd.to_numeric(product['ProductID'], errors='coerce')
    product['ProductSubcategoryID'] = pd.to_numeric(product['ProductSubcategoryID'], errors='coerce')

    subcategory['ProductSubcategoryID'] = pd.to_numeric(subcategory['ProductSubcategoryID'], errors='coerce')

    header_use = header[['SalesOrderID', '_quarter']].dropna(subset=['SalesOrderID', '_quarter'])
    product_use = product[['ProductID', 'ProductSubcategoryID']].dropna(subset=['ProductID', 'ProductSubcategoryID'])
    subcategory_use = subcategory[['ProductSubcategoryID', 'Name']].dropna(subset=['ProductSubcategoryID', 'Name'])

    merged = detail.merge(header_use, on='SalesOrderID', how='inner')
    merged = merged.merge(product_use, on='ProductID', how='inner')
    merged = merged.merge(subcategory_use, on='ProductSubcategoryID', how='inner')

    merged = merged.dropna(subset=['Name', '_quarter'])
    if merged.empty:
        return empty_result()

    merged['_quarter'] = merged['_quarter'].astype(int)
    merged['子类别'] = merged['Name']

    grouped = merged.groupby(['子类别', '_quarter'], as_index=False)['OrderQty'].sum()
    if grouped.empty:
        return empty_result()

    pivot = grouped.pivot(index='子类别', columns='_quarter', values='OrderQty').fillna(0)

    result = pd.DataFrame()
    result['子类别'] = list(pivot.index)

    for q in [1, 2, 3, 4]:
        col = 'Q' + str(q)
        if q in pivot.columns:
            result[col] = pivot[q].to_numpy()
        else:
            result[col] = 0
        result[col] = pd.to_numeric(result[col], errors='coerce').fillna(0).astype(int)

    result['全年合计'] = result[['Q1', 'Q2', 'Q3', 'Q4']].sum(axis=1).astype(int)

    result = result.sort_values(
        by=['全年合计', '子类别'],
        ascending=[False, True],
        kind='mergesort'
    ).reset_index(drop=True)

    if len(result) > 0:
        result['销量排名'] = result['全年合计'].rank(method='min', ascending=False).astype(int)
    else:
        result['销量排名'] = pd.Series(dtype=int)

    total_all = result['全年合计'].sum()
    if total_all == 0:
        result['销量占比'] = 0.0
    else:
        result['销量占比'] = (result['全年合计'] / total_all).round(4)

    return result[columns]