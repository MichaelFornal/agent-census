import numpy as np

from pipeline.cluster import assign_noise, nn_distance, two_level
from pipeline.embed import HashEmbedder

THEMES = (["Uses Claude to build and maintain a web application."] * 4
          + ["Uses Claude to clean and validate CSV data."] * 2
          + ["Uses Claude to help write and outline a novel."] * 2
          + ["Uses Claude to manage cloud infrastructure safely.", "Uses Claude to release plugin versions.",
             "Uses Claude to summarize meeting notes."])


def test_hash_embedder_is_deterministic_and_normalized():
    a, b = HashEmbedder().encode(THEMES), HashEmbedder().encode(THEMES)
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)


def test_small_input_separates_themes_without_level_two():
    l1, l2 = two_level(HashEmbedder().encode(THEMES))
    assert len(set(l1[:4].tolist())) == 1 and l1[0] != -1
    assert l1[4] == l1[5] and l1[4] not in (-1, l1[0])
    assert l1[6] == l1[7] and l1[6] not in (-1, l1[0], l1[4])
    assert (l2 == -1).all()


def test_large_input_goes_through_umap_and_finds_both_blobs():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.05, (40, 16))
    a[:, 0] += 1
    b = rng.normal(0, 0.05, (40, 16))
    b[:, 1] += 1
    X = np.vstack([a, b])
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    raw, _ = two_level(X)  # leaf selection leaves noise by design; S7 assigns it to the nearest domain
    l1 = assign_noise(X, raw)
    mode = lambda xs: max(set(xs.tolist()), key=xs.tolist().count)
    assert mode(l1[:40]) != -1 and mode(l1[40:]) != -1 and mode(l1[:40]) != mode(l1[40:])


def test_single_point_is_noise():
    l1, l2 = two_level(HashEmbedder().encode(["alone"]))
    assert l1.tolist() == [-1] and l2.tolist() == [-1]


def test_nn_distance():
    X = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert np.allclose(nn_distance(X), [0.0, 0.0, 1.0])


def test_assign_noise_moves_only_points_near_a_centroid():
    X = np.array([[1.0, 0.0], [0.99, 0.141], [0.0, 1.0], [0.995, 0.0998], [-1.0, 0.0]])
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    labels = np.array([0, 0, 1, -1, -1])
    assert assign_noise(X, labels, min_sim=0.9).tolist() == [0, 0, 1, 0, -1]
    assert assign_noise(X, np.full(5, -1)).tolist() == [-1] * 5
