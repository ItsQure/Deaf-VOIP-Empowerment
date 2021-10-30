import pandas as pd
import numpy as np
from torch.utils.data import Dataset
from .electricity_utils import *
from .utils import *


class ElectricityDataset(Dataset):
    @staticmethod
    def get_split(data_folder, num_encoder_steps=7 * 24):
        data_path = os.path.join(data_folder, 'LD2011_2014.txt')
        if not os.path.isfile(data_path):
            ElectricityDataset.download(data_path)
        formatter = ElectricityFormatter()
        train, val, test = formatter.split_data(
            ElectricityDataset.aggregating_to_hourly_data(
                pd.read_csv(data_path, index_col=0, sep=';', decimal=',')
            )
        )
        return [ElectricityDataset(data, formatter, num_encoder_steps) for data in [train, val, test]]

    def __init__(self, data, formatter, num_encoder_steps):
        super().__init__()
        self.formatter = formatter
        self.num_encoder_steps = num_encoder_steps
        self.data = data.reset_index(drop=True)
        self.data_index, self.col_mappings = self.build_data_index(self.data)

    def __len__(self):
        return self.data_index.shape[0]

    def __getitem__(self, idx):
        _data_index = self.data.iloc[self.data_index.init_abs.iloc[idx]:self.data_index.end_abs.iloc[idx]]

        data_map = {}
        for k in self.col_mappings:
            cols = self.col_mappings[k]

            if k not in data_map:
                data_map[k] = [_data_index[cols].values]
            else:
                data_map[k].append(_data_index[cols].values)

        for k in data_map:
            data_map[k] = np.concatenate(data_map[k], axis=0)

        scaler = self.formatter._target_scaler[data_map["identifier"][0][0]]
        outputs = data_map['outputs'][self.num_encoder_steps:, 0]
        return data_map['inputs'], outputs, scaler.mean_, scaler.scale_

    def build_data_index(self, data):
        column_definition = self.formatter._column_definition
        col_mappings = {
            'identifier': [get_single_col_by_input_type(InputTypes.ID, column_definition)],
            'time': [get_single_col_by_input_type(InputTypes.TIME, column_definition)],
            'outputs': [get_single_col_by_input_type(InputTypes.TARGET, column_definition)],
            'inputs': [tup[0] for tup in column_definition if tup[2] not in {InputTypes.ID, InputTypes.TIME}]
        }
        lookback = self.formatter.get_time_steps()
        data_index = self.get_index_filtering(data, col_mappings["identifier"], col_mappings["outputs"], lookback)
        group_size = data.groupby(col_mappings["identifier"]).apply(lambda x: x.shape[0]).mean()
        data_index = data_index[data_index.end_rel < group_size].reset_index()
        return data_index, col_mappings

    def get_index_filtering(self, data, id_col, target_col, lookback):
        g = data.groupby(id_col)

        df_index_abs = g[target_col].transform(lambda x: x.index+lookback) \
                        .reset_index() \
                        .rename(columns={'index': 'init_abs', target_col[0]: 'end_abs'})
        df_index_rel_init = g[target_col].transform(lambda x: x.reset_index(drop=True).index) \
                        .rename(columns={target_col[0]: 'init_rel'})
        df_index_rel_end = g[target_col].transform(lambda x: x.reset_index(drop=True).index+lookback) \
                        .rename(columns={target_col[0]: 'end_rel'})
        df_total_count = g[target_col].transform(lambda x: x.shape[0] - lookback + 1) \
                        .rename(columns = {target_col[0]: 'group_count'})

        return pd.concat([df_index_abs,
                          df_index_rel_init,
                          df_index_rel_end,
                          data[id_col],
                          df_total_count], axis = 1).reset_index(drop = True)

    @staticmethod
    def aggregating_to_hourly_data(df):
        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)

        # Used to determine the start and end dates of a series
        output = df.resample('1h').mean().replace(0., np.nan)

        earliest_time = output.index.min()

        df_list = []
        for label in output:
            print('Processing {}'.format(label))
            srs = output[label]

            start_date = min(srs.fillna(method='ffill').dropna().index)
            end_date = max(srs.fillna(method='bfill').dropna().index)

            active_range = (srs.index >= start_date) & (srs.index <= end_date)
            srs = srs[active_range].fillna(0.)

            tmp = pd.DataFrame({'power_usage': srs})
            date = tmp.index
            tmp['t'] = (date - earliest_time).seconds / 60 / 60 + (
                date - earliest_time).days * 24
            tmp['days_from_start'] = (date - earliest_time).days
            tmp['categorical_id'] = label
            tmp['date'] = date
            tmp['id'] = label
            tmp['hour'] = date.hour
            tmp['day'] = date.day
            tmp['day_of_week'] = date.dayofweek
            tmp['month'] = date.month

            df_list.append(tmp)

        output = pd.concat(df_list, axis=0, join='outer').reset_index(drop=True)

        output['categorical_id'] = output['id'].copy()
        output['hours_from_start'] = output['t']
        output['categorical_day_of_week'] = output['day_of_week'].copy()
        output['categorical_hour'] = output['hour'].copy()

        # Filter to match range used by other academic papers
        output = output[(output['days_from_start'] >= 1096)
                        & (output['days_from_start'] < 1346)].copy()
        return output

    @staticmethod
    # def download(data_folder, csv_path):
    def download(data_path):
        data_folder, csv_path = os.path.split(data_path)
        url = 'https://archive.ics.uci.edu/ml/machine-learning-databases/00321/LD2011_2014.txt.zip'
        zip_path = data_path + ".zip"
        download_and_unzip(url, zip_path, data_path, data_folder)
        print('Done.')
