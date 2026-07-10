import os
from typing import List, Optional, Generator
import xarray as xr
from arkitekt_next import register, easy, progress
from mikro_next.api.schema import (
    Image,
    from_array_like,
    File,
    create_instrument,
    create_stage,
    Stage,
    PartialRGBViewInput,
    PartialAffineTransformationViewInput,
    PartialOpticsViewInput,
    PartialChannelViewInput,
    PartialFileViewInput,
    PartialLightpathViewInput,
    ElementKind,
    LightpathGraphInput,
    OpticalElementInput,
    PortRole,
    ChannelKind,
    LightEdgeInput,
    LightPortInput,
)
from bioio_bioformats.biofile import BioFile
import logging
from scyjava import config
from bioio import BioImage
import numpy as np
from ome_types.model import UnitsLength, Channel as OmeChannel, Image as OmeImage, OME
from uuid import uuid4
logger = logging.getLogger(__name__)
x = config


def load_as_xarray(image: BioImage, scene: int):
    image.set_scene(scene)

    xarray_dask = image.xarray_dask_data

    # FIX: Handle RGB samples stored in 'S' dimension (common in JPEGs/TIFFs)
    # Instead of selecting index 0 (which deletes data), we check if we need to map S to C
    if "S" in xarray_dask.dims:
        if xarray_dask.sizes["S"] > 1 and xarray_dask.sizes["C"] == 1:
            # Squeeze C and rename S to C (e.g., 1 Channel with 3 Samples -> 3 Channels)
            array = xarray_dask.squeeze("C").rename({"S": "c"})
        else:
            # Fallback for actual spectral data or if not RGB
            array = xarray_dask.isel(S=0)
    else:
        array = xarray_dask

    # Standardize dimension names to lowercase
    image_data = array.rename({
        k: k.lower() 
        for k in array.dims 
        if k in ["C", "T", "Z", "X", "Y"]
    })

    # Ensure all dimensions exist
    desired_dims = ["c", "t", "z", "y", "x"]
    
    # Expand missing dims
    for d in desired_dims:
        if d not in image_data.dims:
            image_data = image_data.expand_dims({d: 1})

    image_data = image_data.transpose(*desired_dims)

    x = xr.DataArray(image_data.data, dims=list("ctzyx"))
    return x


def convert_float_to_correct_micrometer(x, unit: str):
    if unit in [UnitsLength.REFERENCEFRAME]:
        return x

    if unit in ["micrometer", "micrometers", "µm", "um", UnitsLength.MICROMETER]:
        return x
    elif unit in ["nanometer", "nanometers", "nm", UnitsLength.NANOMETER]:
        return x / 1000
    elif unit in ["mm", "millimeter", "millimeters", UnitsLength.MILLIMETER]:
        return x * 1000
    elif unit in ["m", "meter", "meters", UnitsLength.METER]:
        return x * 1_000_000
    else:
        # Default fallback for None or unknown
        return x


def create_light_graph(
    channel: OmeChannel, image: OmeImage, ome: OME
) -> LightpathGraphInput | None:
    instrument_ref = image.instrument_ref

    if instrument_ref is None:
        return None

    found_instrument = [
        instrument
        for instrument in ome.instruments
        if instrument.id == instrument_ref.id
    ]
    if len(found_instrument) != 1:
        return None

    instrument = found_instrument[0]

    detector_map: dict[str, OpticalElementInput] = {}
    objective_map: dict[str, OpticalElementInput] = {}
    filters_map: dict[str, OpticalElementInput] = {}
    laser_map: dict[str, OpticalElementInput] = {}

    for detector in instrument.detectors:
        detector_map[detector.id] = OpticalElementInput(
            id=detector.id,
            kind=ElementKind.DETECTOR,
            manufacturer=detector.manufacturer,
            model=detector.model,
            label=f"{detector.manufacturer} {detector.model}",
            ports=[
                LightPortInput(
                    id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE
                ),
            ],
        )

    for laser in instrument.lasers:
        laser_map[laser.id] = OpticalElementInput(
            id=laser.id,
            kind=ElementKind.LASER,
            manufacturer=laser.manufacturer,
            model=laser.model,
            label=f"{laser.manufacturer} {laser.model} {laser.wavelength}",
            nominal_wavelength=str(laser.wavelength) + " nm" if laser.wavelength is not None else None,
            ports=[
                LightPortInput(
                    id="out", name="Light Out", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE
                ),
            ],
        )

    for objective in instrument.objectives:
        objective_map[objective.id] = OpticalElementInput(
            id=objective.id,
            kind=ElementKind.OBJECTIVE,
            label=f"{objective.manufacturer} {objective.model}",
            magnification=objective.nominal_magnification or objective.calibrated_magnification,
            numericalAperture=objective.lens_na,
            manufacturer=objective.manufacturer,
            model=objective.model,
            workingDistance=str(objective.working_distance) + " mm" if objective.working_distance is not None else None,
            ports=[
                LightPortInput(
                    id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE
                ),
                LightPortInput(
                    id="out", name="Light In", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE
                ),
            ],
        )

    for filter_obj in instrument.filters:
        filters_map[filter_obj.id] = OpticalElementInput(
            id=filter_obj.id,
            kind=ElementKind.FILTER,
            label=filter_obj.manufacturer or filter_obj.id,
            manufacturer=filter_obj.manufacturer,
            model=filter_obj.model,
            ports=[
                LightPortInput(
                    id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE
                ),
                LightPortInput(
                    id="out", name="Light In", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE
                ),
            ],
        )

    light_sources: list[OpticalElementInput] = list(laser_map.values())

    if image.objective_settings:
        objective = objective_map.get(image.objective_settings.id)
        if not objective:
             objective = OpticalElementInput(
                id="unknown_obj",
                kind=ElementKind.OBJECTIVE,
                label="Unknown Objective",
                ports=[LightPortInput(id="in", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE), LightPortInput(id="out", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE)]
            )
    else:
        objective = OpticalElementInput(
            id="pinhole",
            kind=ElementKind.OBJECTIVE,
            label="Unknown Objective",
            ports=[
                LightPortInput(id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE),
                LightPortInput(id="out", name="Light In", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE),
            ],
        )

    if channel.pinhole_size:
        pinhole = OpticalElementInput(
            id="pinhole",
            kind=ElementKind.PINHOLE,
            diameter=str(channel.pinhole_size) + " μm" if channel.pinhole_size is not None else None,
            label="A pinhole",
            ports=[
                LightPortInput(id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE),
                LightPortInput(id="out", name="Light In", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE),
            ],
        )
    else:
        pinhole = None

    excitation_filters = []
    emission_filters = []

    if channel.light_path:
        for exc_filter_ref in channel.light_path.excitation_filters:
            if exc_filter_ref.id in filters_map:
                excitation_filters.append(filters_map[exc_filter_ref.id])

        for emi_filter_ref in channel.light_path.emission_filters:
             if emi_filter_ref.id in filters_map:
                emission_filters.append(filters_map[emi_filter_ref.id])

    if channel.detector_settings and channel.detector_settings.id in detector_map:
        detector = detector_map[channel.detector_settings.id]
        detector.model_config["frozen"] = False
        if channel.detector_settings.gain is not None:
            detector.gain = channel.detector_settings.gain
    else:
        detector = OpticalElementInput(
            id="detector",
            kind=ElementKind.DETECTOR,
            label=" A default Detector",
            ports=[
                LightPortInput(id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE),
            ],
        )

    edges = []
    elements = []
    id_counter = 0

    unifier = OpticalElementInput(
        id="unifier",
        label="A unifier",
        kind=ElementKind.MIRROR,
        ports=[
            LightPortInput(id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE),
            LightPortInput(id="out", name="Light In", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE),
        ],
    )
    elements.append(unifier)

    for light_source in light_sources:
        elements.append(light_source)
        edges.append(
            LightEdgeInput(
                id=str(id_counter),
                sourceElementId=light_source.id,
                sourcePortId=light_source.ports[0].id,
                targetElementId=unifier.id,
                targetPortId=unifier.ports[0].id,
            )
        )
        id_counter += 1

    latest_connector = unifier

    for ex_filter in excitation_filters:
        elements.append(ex_filter)
        edges.append(
            LightEdgeInput(
                id=str(id_counter),
                sourceElementId=latest_connector.id,
                sourcePortId=latest_connector.ports[1].id,
                targetElementId=ex_filter.id,
                targetPortId=ex_filter.ports[0].id,
            )
        )
        latest_connector = ex_filter
        id_counter += 1

    elements.append(objective)
    edges.append(
        LightEdgeInput(
            id=str(id_counter),
            sourceElementId=latest_connector.id,
            sourcePortId=latest_connector.ports[1].id,
            targetElementId=objective.id,
            targetPortId=objective.ports[0].id,
        )
    )
    id_counter += 1

    sample = OpticalElementInput(
        id="sample",
        label="The Sample",
        kind=ElementKind.SAMPLE,
        ports=[
            LightPortInput(id="in", name="Light In", role=PortRole.INPUT, channel=ChannelKind.FREE_SPACE),
            LightPortInput(id="out", name="Light Out", role=PortRole.OUTPUT, channel=ChannelKind.FREE_SPACE),
        ],
    )
    elements.append(sample)
    edges.append(
        LightEdgeInput(
            id=str(id_counter),
            sourceElementId=objective.id,
            sourcePortId=objective.ports[1].id,
            targetElementId=sample.id,
            targetPortId=sample.ports[0].id,
        )
    )
    id_counter += 1

    after_sample = sample
    if pinhole:
        elements.append(pinhole)
        edges.append(
            LightEdgeInput(
                id=str(id_counter),
                sourceElementId=sample.id,
                sourcePortId=sample.ports[1].id,
                targetElementId=pinhole.id,
                targetPortId=pinhole.ports[0].id,
            )
        )
        id_counter += 1
        after_sample = pinhole

    latest_em_filter_bank_connector = after_sample
    for em_filter in emission_filters:
        elements.append(em_filter)
        edges.append(
            LightEdgeInput(
                id=str(id_counter),
                sourceElementId=latest_em_filter_bank_connector.id,
                sourcePortId=latest_em_filter_bank_connector.ports[1].id,
                targetElementId=em_filter.id,
                targetPortId=em_filter.ports[0].id,
            )
        )
        id_counter += 1
        latest_em_filter_bank_connector = em_filter

    elements.append(detector)
    edges.append(
        LightEdgeInput(
            id=str(id_counter),
            sourceElementId=latest_em_filter_bank_connector.id,
            sourcePortId=latest_em_filter_bank_connector.ports[1].id,
            targetElementId=detector.id,
            targetPortId=detector.ports[0].id,
        )
    )
    id_counter += 1

    return LightpathGraphInput(elements=elements, edges=edges)


@register(logo="ome.png")
def convert_omero_file(
    file: File,
    stage: Optional[Stage],
) -> Generator[Image, None, None]:

    assert file.store, "No File Provided"

    progress(0, "Downloading File")
    f = file.store.download(file.name)

    try:
        progress(10, "Downloaded File. Inspecting Metadata")
        
        aics_image = BioImage(f)
        meta = BioFile(f, series=0).ome_metadata
        instrument_map = dict()
        stage = stage or create_stage(f"New Stage for {file.name}")

        for instrument in meta.instruments:
            if instrument.id and instrument.microscope:
                instrument_map[instrument.id] = create_instrument(
                    name=instrument.microscope.serial_number or instrument.id,
                    serial_number=instrument.microscope.serial_number or instrument.id,
                    model=instrument.microscope.model or instrument.id,
                )

        amount_images = len(aics_image.scenes)
        start_percent = np.linspace(10, 100, amount_images + 1)

        for index, scene in enumerate(aics_image.scenes):
            image = meta.images[index]
            pixels = image.pixels
            
            progress(start_percent[index], f"Processing Scene {index+1}/{amount_images}")

            # Load the array FIRST to check actual dimensions
            array = load_as_xarray(aics_image, scene)
            array = array.transpose("c", "t", "z", "y", "x").compute()
            
            # --- VIEW GENERATION LOGIC ---
            rgb_views = []
            channel_views = []
            lightgraph_views = []

            # FIX: Check if we have an "Implicit RGB" situation (1 Meta Channel vs 3 Array Channels)
            is_implicit_rgb = len(pixels.channels) == 1 and array.sizes["c"] == 3

            if is_implicit_rgb:
                print(f"Detected Implicit RGB (JPEG style). Mapping C=0..2 to RGB.")
                # Create one RGB View covering the first 3 channels
                rgb_views.append(
                    PartialRGBViewInput(
                        cMin=0,
                        cMax=1,
                        colorMap="RED",
                        baseColor=(255, 0, 0)
                    )
                )
                rgb_views.append(
                    PartialRGBViewInput(
                        cMin=1,
                        cMax=2,
                        colorMap="GREEN",
                        baseColor=(0, 255, 0)
                    )
                )
                rgb_views.append(
                    PartialRGBViewInput(
                        cMin=2,
                        cMax=3,
                        colorMap="BLUE",
                        baseColor=(0, 0, 255)
                    )
                )
                # Create simple labels for the channels
                for i, name in enumerate(["Red", "Green", "Blue"]):
                    channel_views.append(
                        PartialChannelViewInput(
                            name=name,
                            cMin=i,
                            cMax=i+1,
                        )
                    )
            else:
                # Normal Fluorescence/Multi-channel logic
                for channelindex, channel in enumerate(pixels.channels):
                    
                    
                    # Handle Color assigned in OME metadata
                    if channel.color:
                        value = channel.color.as_rgb_tuple() + (255,)
                        rgb_views.append(
                            PartialRGBViewInput(
                                cMin=channelindex,
                                cMax=channelindex + 1,
                                colorMap="INTENSITY",
                                baseColor=value,
                            )
                        )

                    if channel.name:
                        channel_views.append(
                            PartialChannelViewInput(
                                name=channel.name,
                                cMin=channelindex,
                                cMax=channelindex + 1,
                            )
                        )

                    graph = create_light_graph(channel, image, meta)
                    if graph is not None:
                        lightgraph_views.append(
                            PartialLightpathViewInput(
                                graph=graph,
                                cMin=channelindex,
                                cMax=channelindex + 1,
                            )
                        )

            # --- GEOMETRY & OPTICS ---
            
            physical_size_x = pixels.physical_size_x if pixels.physical_size_x else 1
            physical_size_y = pixels.physical_size_y if pixels.physical_size_y else 1
            physical_size_z = pixels.physical_size_z if pixels.physical_size_z else 1

            corrected_physical_size_x = convert_float_to_correct_micrometer(physical_size_x, pixels.physical_size_x_unit)
            corrected_physical_size_y = convert_float_to_correct_micrometer(physical_size_y, pixels.physical_size_y_unit)
            corrected_physical_size_z = convert_float_to_correct_micrometer(physical_size_z, pixels.physical_size_z_unit)

            affine_matrix = np.eye(4)
            affine_matrix[0, 0] = corrected_physical_size_x
            affine_matrix[1, 1] = corrected_physical_size_y
            affine_matrix[2, 2] = corrected_physical_size_z

            if len(pixels.planes) > 0:
                first_plane = pixels.planes[0]
                affine_matrix[0, 3] = convert_float_to_correct_micrometer(first_plane.position_x, first_plane.position_x_unit) if first_plane.position_x else 0
                affine_matrix[1, 3] = convert_float_to_correct_micrometer(first_plane.position_y, first_plane.position_y_unit) if first_plane.position_y else 0
                affine_matrix[2, 3] = convert_float_to_correct_micrometer(first_plane.position_z, first_plane.position_z_unit) if first_plane.position_z else 0

            transformation_views = [
                PartialAffineTransformationViewInput(
                    affine_matrix=affine_matrix,
                    stage=stage,
                )
            ]

            optics_views = []
            if image.instrument_ref:
                ins = instrument_map.get(image.instrument_ref.id, None)
                if ins is not None:
                    optics_views.append(PartialOpticsViewInput(instrument=ins))

            progress(start_percent[index], f"Uploading Scene {index+1}/{amount_images}")
            
            yield from_array_like(
                array,
                name=file.name + " - " + (image.name if image.name else f"({index})"),
                tags=["converted"],
                transformation_views=transformation_views,
                lightpath_views=lightgraph_views,
                optics_views=optics_views,
                channel_views=channel_views,
                rgb_views=rgb_views,
                file_views=[
                    PartialFileViewInput(
                        file=file,
                        seriesIdentifier=image.id,
                    )
                ],
            )

    except Exception as e:
        logger.error(e, exc_info=True)
        raise e

    finally:
        if os.path.exists(f):
            os.remove(f)

if __name__ == "__main__":
    # Example usage (commented out)
    # with easy("test_app") as e:
    #     convert_omero_file(...)
    pass