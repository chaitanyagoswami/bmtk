import os
from six import string_types
import numpy as np

from bmtk.utils import sonata
from bmtk.utils.sonata.config import SonataConfig
from bmtk.utils.reports.compartment import CompartmentReport
from bmtk.utils.reports.compartment import plotting
from bmtk.simulator.utils import simulation_reports


def _get_report(report_path=None, config=None, report_name=None):
    if report_path is not None:
        return report_path, CompartmentReport.load(report=report_path)

    elif config is not None:
        selected_reports = []
        sim_reports = simulation_reports.from_config(config)
        for report in sim_reports:
            if report.module in ['membrane_report', 'multimeter_report']:
                rname = report.report_name
                rfile = report.params['file_name']
                # TODO: Full path should be determined by config/simulation_reports module
                rpath = rfile if os.path.isabs(rfile) else os.path.join(report.params['tmp_dir'], rfile)
                if report_name is not None and report_name == rname:
                        selected_reports.append((rname, CompartmentReport.load(rpath)))
                elif report_name is None:
                    selected_reports.append((rname, CompartmentReport.load(rpath)))

        if len(selected_reports) == 0:
            msg = 'Could not find a report '
            msg += '' if report_name is None else 'with report_name "{}"'.format(report_name)
            msg += ' from configuration file. . Use "report_path" parameter instead.'
            raise ValueError(msg)

        elif len(selected_reports) > 1:
            avail_reports = ', '.join(s[0] for s in selected_reports)
            raise ValueError('Configuration file contained multiple "membrane_reports", use "report_name" or'
                             '"report_path" to pick which one to plot. Option values: {}'.format(avail_reports))

        else:
            return selected_reports[0]

    else:
        raise AttributeError('Could not find a compartment report SONATA file. Please user "config_file" or '
                             '"report_path" options.')


def _find_nodes(population, config=None, nodes_file=None, node_types_file=None):
    if nodes_file is not None:
        network = sonata.File(data_files=nodes_file, data_type_files=node_types_file)
        if population not in network.nodes.population_names:
            raise ValueError('node population "{}" not found in {}'.format(population, nodes_file))
        return network.nodes[population]

    elif config is not None:
        for nodes_grp in config.nodes:
            network = sonata.File(data_files=nodes_grp['nodes_file'], data_type_files=nodes_grp['node_types_file'])
            if population in network.nodes.population_names:
                return network.nodes[population]

    raise ValueError('Could not find nodes file with node population "{}".'.format(population))


def plot_traces(report_path=None, config_file=None, report_name=None, population=None, group_by=None,
                group_excludes=None, nodes_file=None, node_types_file=None,
                node_ids=None, sections='origin', average=False, times=None, title=None,
                show_legend=None, show=True):
    sonata_config = SonataConfig.from_json(config_file) if config_file else None
    report_name, cr = _get_report(report_path=report_path, config=sonata_config, report_name=report_name)

    if population is None:
        pops = cr.populations
        if len(pops) > 1:
            raise ValueError('Report {} contains more than population of nodes ({}). Use population parameter'.format(
                report_name, pops
            ))
        population = pops[0]

    if title is None:
        title = '{} ({})'.format(report_name, population)

    # Create node-groups
    if group_by is not None:
        node_groups = []
        nodes = _find_nodes(population=population, config=sonata_config, nodes_file=nodes_file,
                            node_types_file=node_types_file)

        grouped_df = None
        for grp in nodes.groups:
            if group_by in grp.all_columns:
                grp_df = grp.to_dataframe()
                grp_df = grp_df[['node_id', group_by]]
                grouped_df = grp_df if grouped_df is None else grouped_df.append(grp_df, ignore_index=True)

        if grouped_df is None:
            raise ValueError('Could not find any nodes with group_by attribute "{}"'.format(group_by))

        # Convert from string to list so we can always use the isin() method for filtering
        if isinstance(group_excludes, string_types):
            group_excludes = [group_excludes]
        elif group_excludes is None:
            group_excludes = []

        for grp_key, grp in grouped_df.groupby(group_by):
            if grp_key in group_excludes:
                continue
            node_groups.append({'node_ids': np.array(grp['node_id']), 'label': grp_key})

        if len(node_groups) == 0:
            exclude_str = ' excluding values {}'.format(', '.join(group_excludes)) if len(group_excludes) > 0 else ''
            raise ValueError('Could not find any node-groups using group_by="{}"{}.'.format(group_by, exclude_str))

    else:
        node_groups = None

    return plotting.plot_traces(
        report=cr,
        population=population,
        node_ids=node_ids,
        sections=sections,
        average=average,
        node_groups=node_groups,
        times=times,
        title=title,
        show_legend=show_legend,
        show=show
    )
