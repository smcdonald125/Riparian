import os
import geopandas as gpd 
import pandas as pd
import multiprocessing as mp

class Estuary:
    def run_estuary(folder, local_folder):
        outpath = f"{local_folder}/data/input/DelawareAtlantic_1m.shp"
        # counties with area in the Delaware Bay or Atlantic not covered by CBW VIMS shoreline
        cfs = ['acco_51001', 'kent_10001', 'newc_10003', 'nort_51131', 'suss_10005', 'virg_51810', 'worc_24047'] 
        est_list = []
        for cf in cfs:
            tmp = Estuary.getEstuaryMarine(folder, cf)
            if len(tmp) > 0:
                est_list.append(tmp.copy())
                print(f"{cf}: added {len(tmp)} records")
            else:
                print(f"{cf}: no records")
            del tmp

        # concat into one dataframe
        est_gdf = pd.concat(est_list).pipe(gpd.GeoDataFrame)
        del est_list
        est_gdf.crs = "EPSG:5070"

        est_gdf.to_file(outpath)

    def getEstuaryMarine(folder, cf):
        # set path and verify data exists
        lotic_path = f"{folder}/{cf}/input/wetlands/water.gpkg"
        if not os.path.isfile(lotic_path):
            return []

        # read in water for county
        gdf = gpd.read_file(lotic_path, layer='water')

        # select Estuarine/Marine class
        gdf = gdf[['lu_code', 'geometry']]
        gdf.loc[:, 'lu_code'] = gdf.lu_code.astype(int)
        gdf = gdf[gdf['lu_code']==1100]

        # return 
        return gdf

class Lotic:
    def run_lotic(folder, local_folder, threshold, facet_path):
        outpath = f"{local_folder}/data/input/lotic_reservoirs_1m.shp"
        # read in lotic polys for each county
        lotic_list = []
        for cf in os.listdir(folder):
            tmp = Lotic.get_lotic_and_reservoirs(folder, cf, threshold)
            if len(tmp) > 0:
                lotic_list.append(tmp.copy())
                print(f"{cf}: added {len(tmp)} records")
            else:
                print(f"{cf}: no records")
            del tmp

        # concat into one dataframe
        lotic_gdf = pd.concat(lotic_list).pipe(gpd.GeoDataFrame)
        del lotic_list
        lotic_gdf.crs = "EPSG:5070"

        # remove features not connected to the stream network
        lotic_gdf = Lotic.remove_disconnected_features(facet_path, lotic_gdf)

        # write results
        lotic_gdf.to_file(outpath)

    def get_lotic_and_reservoirs(folder, cf, threshold):
        # set path and verify data exists
        lotic_path = f"{folder}/{cf}/input/wetlands/water.gpkg"
        if not os.path.isfile(lotic_path):
            return []

        # read in water for county
        gdf = gpd.read_file(lotic_path, layer='water')

        # select lotic water and reservoirs
        gdf = gdf[['lu_code', 'geometry']]
        gdf.loc[:, 'lu_code'] = gdf.lu_code.astype(int)
        gdf = gdf[gdf['lu_code'].isin([1300, 1210])]

        # remove small lotic water features
        gdf.loc[:, 'acres'] = gdf.geometry.area / 4046.86
        gdf = gdf[(gdf['lu_code']==1210)|((gdf['lu_code']==1300)&(gdf['acres']>=threshold))]
        
        # return lotic water in county
        return gdf[['lu_code','acres','geometry']]

    def remove_disconnected_features(facet_path, lotic_gdf):
        """
        Method: remove_disconnected_features()
        Purpose: Intersect the lotic and reservoirs data with FACET to remove ponds, lakes,
                and other features not connected to the stream network.
        Params: facet_path - path to FACET streams
                lotic_gdf - geodataframe of lotic water and reservoirs from LULC
        Returns: lotic_gdf
        """
        # 1. read in FACET streams
        facet = gpd.read_file(facet_path, bbox=lotic_gdf.envelope)

        # 2. Get list of lotic features that intersect FACET
        lotic_gdf.loc[:, 'id'] = [int(x) for x in range(len(lotic_gdf))]
        lotic_ids = sjoin_mp(lotic_gdf[['id','geometry']], 'intersects', facet[['geometry']])
        lotic_ids = list(lotic_ids['id'])
        del facet

        # 3. select lotic features intersecting facet
        print(f"Removing {len(lotic_gdf) - len(lotic_ids)} disconnected features")
        lotic_gdf = lotic_gdf[lotic_gdf['id'].isin(lotic_ids)]
        del lotic_ids

        return lotic_gdf

class FACET:
    def clean_facet(local_folder, facet_path):
        outpath = f"{local_folder}/data/input/FACET_100k_gapfilled_cleaned.shp"
        # 1. read in facet
        gdf = gpd.read_file(facet_path)
        gdf.loc[:, 'id'] = [int(x) for x in range(len(gdf))]
        gdf.loc[:, 'len'] = gdf['geometry'].length
        print(gdf[['geometry']])

        # 2. sjoin segments
        df = sjoin_mp(gdf[['id','geometry']], 'intersects', gdf[['id','geometry']], ['id_left','id_right'])

        # 3. remove records where ids are the same - records are duplicated, only need unique list of left or right
        print(df)
        df = df[df['id_left'] != df['id_right']][['id_left']]
        print(df)
        ids = list(df['id_left'].unique())
        del df

        # 4. remove records that are not touching another stream segment
        print(f"Removing {len(gdf) - len(ids)} records from {len(gdf)} stream segments...")
        gdf = gdf[gdf['id'].isin(ids)]
        del ids

        # 5. write results
        gdf.to_file(outpath)

def sjoin_mp(df1, sjoin_op, df2, cols):
    """
    Method: sjoin_mp6()
    Purpose: Chunk and mp a sjoin function on specified geodataframes for specified operation,
            retaining specified columns.
    Params: df1 - geodataframe of data to chunk and sjoin (left gdf)
            batch_size - integer value of max number of records to include in each chunk
            sjoin_op - string of sjoin operation to use; 'intersects', 'within', 'contains'
            cols - list of column names to retain
            df2 - geodataframe of data to sjoin (right gdf)
    Returns: sjoinSeg - df (or gdf) of sjoined data, with sjoin columns retained
    """
    NUM_CPUS, batch_size = 6, (int(len(df1) / 6) + 1)
    print(f"{NUM_CPUS} batches of {batch_size} for {len(df1)} records")

    chunk_iterator = []
    for i in range(NUM_CPUS):
        mn, mx = i * batch_size, (i + 1) * batch_size
        gdf_args = df1[mn:mx], df2, sjoin_op, cols
        chunk_iterator.append(gdf_args)

    pool = mp.Pool(processes=NUM_CPUS)
    sj_results = pool.map(sjoin, chunk_iterator)
    pool.close()
    sj_results = pd.concat(sj_results)
    return sj_results

def sjoin(args):
    """
    Method: sjoin_mp_pt5()
    Purpose: Run sjoin on specified geodataframes for specified operation,
            retaining specified columns.
    Params: args - tuple of arguments
                df1 - geodataframe of data to sjoin (left gdf)
                df2 - geodataframe of data to sjoin (right gdf)
                sjoin_op - string of sjoin operation to use; 'intersects', 'within', 'contains'
                sjoinCols - string of column names to retain, separated by a space
    Returns: sjoinSeg - df (or gdf) of sjoined data, with sjoin columns retained
    """
    df1, df2, sjoin_op, cols = args 
    sjoinSeg = gpd.sjoin(df1, df2, how='inner', op=sjoin_op)
    sjoinSeg.drop_duplicates(inplace=True)
    return sjoinSeg[cols]

if __name__=="__main__":
    # paths
    folder = r'X:/landuse/version2'
    local_folder = r'C:/Users/smcdonald/Documents/Data/Riparian'
    facet_path = f"{local_folder}/data/input/FACET_NHD100k_aligned_w_gaps_filled_v1.shp"
    threshold = 25 # min acres

    # run lotic
    Lotic.run_lotic(folder, local_folder, threshold, facet_path)

    # run Estuary Marine for Delaware Bay and Atlantic
    Estuary.run_estuary(folder, local_folder)

    # remove FACET segments that are disconnected from the rest of the network
    FACET.clean_facet(local_folder, facet_path)